from typing import Tuple, Optional

import torch
from torch import nn
import numpy as np
from PIL import ImageColor
import albumentations as A
from albumentations.core.transforms_interface import ImageOnlyTransform
import cv2

from rastervision.pipeline.config import ConfigError


def color_to_triple(color: Optional[str] = None) -> Tuple[int, int, int]:
    """Given a PIL ImageColor string, return a triple of integers
    representing the red, green, and blue values.

    If color is None, return a random color.

    Args:
         color: A PIL ImageColor string

    Returns:
         An triple of integers

    """
    if color is None:
        r = np.random.randint(0, 0x100)
        g = np.random.randint(0, 0x100)
        b = np.random.randint(0, 0x100)
        return (r, g, b)
    else:
        return ImageColor.getrgb(color)


def compute_conf_mat(out, y, num_labels):
    labels = torch.arange(0, num_labels).to(out.device)
    return ((out == labels[:, None]) & (y == labels[:, None, None])).sum(
        dim=2, dtype=torch.float32)


def compute_conf_mat_metrics(conf_mat, label_names, eps=1e-6):
    # eps is to avoid dividing by zero.
    eps = torch.tensor(eps)
    conf_mat = conf_mat.cpu()
    gt_count = conf_mat.sum(dim=1)
    pred_count = conf_mat.sum(dim=0)
    total = conf_mat.sum()
    true_pos = torch.diag(conf_mat)
    precision = true_pos / torch.max(pred_count, eps)
    recall = true_pos / torch.max(gt_count, eps)
    f1 = (2 * precision * recall) / torch.max(precision + recall, eps)

    weights = gt_count / total
    weighted_precision = (weights * precision).sum()
    weighted_recall = (weights * recall).sum()
    weighted_f1 = ((2 * weighted_precision * weighted_recall) / torch.max(
        weighted_precision + weighted_recall, eps))

    metrics = {
        'avg_precision': weighted_precision.item(),
        'avg_recall': weighted_recall.item(),
        'avg_f1': weighted_f1.item()
    }
    for ind, label in enumerate(label_names):
        metrics.update({
            '{}_precision'.format(label): precision[ind].item(),
            '{}_recall'.format(label): recall[ind].item(),
            '{}_f1'.format(label): f1[ind].item(),
        })
    return metrics


def validate_albumentation_transform(tf: dict):
    """ Validate a serialized albumentation transform. """
    if tf is not None:
        try:
            A.from_dict(tf)
        except Exception:
            raise ConfigError('The given serialization is invalid. Use '
                              'A.to_dict(transform) to serialize.')
    return tf


class SplitTensor(nn.Module):
    """ Wrapper around `torch.split` """

    def __init__(self, size_or_sizes, dim):
        super().__init__()
        self.size_or_sizes = size_or_sizes
        self.dim = dim

    def forward(self, X):
        return X.split(self.size_or_sizes, dim=self.dim)


class Parallel(nn.ModuleList):
    """ Passes inputs through multiple `nn.Module`s in parallel.
        Returns a tuple of outputs.
    """

    def __init__(self, *args):
        super().__init__(args)

    def forward(self, xs):
        if isinstance(xs, torch.Tensor):
            return tuple(m(xs) for m in self)
        assert len(xs) == len(self)
        return tuple(m(x) for m, x in zip(self, xs))


class AddTensors(nn.Module):
    """ Adds all its inputs together. """

    def forward(self, xs):
        return sum(xs)


class MinMaxNormalize(ImageOnlyTransform):
    """Albumentations transform that normalizes image to desired min and max values.

    This will shift and scale the image appropriately to achieve the desired min and
    max.
    """

    def __init__(
            self,
            min_val=0.0,
            max_val=1.0,
            dtype=cv2.CV_32F,
            always_apply=False,
            p=1.0,
    ):
        """Constructor.

        Args:
            min_val: the minimum value that output should have
            max_val: the maximum value that output should have
            dtype: the dtype of output image
        """
        super(MinMaxNormalize, self).__init__(always_apply, p)
        self.min_val = min_val
        self.max_val = max_val
        self.dtype = dtype

    def _apply_on_channel(self, image, **params):
        out = cv2.normalize(
            image,
            None,
            self.min_val,
            self.max_val,
            cv2.NORM_MINMAX,
            dtype=self.dtype)
        # We need to clip because sometimes values are slightly less or more than
        # min_val and max_val due to rounding errors.
        return np.clip(out, self.min_val, self.max_val)

    def apply(self, image, **params):
        if image.ndim <= 2:
            return self._apply_on_channel(image, **params)

        assert image.ndim == 3

        chs = [
            self._apply_on_channel(ch, **params)
            for ch in image.transpose(2, 0, 1)
        ]
        out = np.stack(chs, axis=2)
        return out

    def get_transform_init_args_names(self):
        return ('min_val', 'max_val', 'dtype')
