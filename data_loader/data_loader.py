import pytorch_lightning as pl
from torchvision import transforms
from torch.utils.data import DataLoader, Subset
from torchvision.transforms import functional as F

import numpy as np
import math
from .config import LABEL_FILE, PUBLIC_TEST_DIR, TRAIN_DIR
from .dataset import HandWritttenDataset, collate_fn_ctc, HandWrittenDatasetV2, collate_fn
from .augment import *

def get_data(
        batch_size: int = 64,
        seed: int = 42,
        args=None
    ):
    """
    Get the train, validation and test data loaders

    Arguments:
    ----------

    batch_size: int (default: 64)
        The batch size to use for the data loaders

    seed: int (default: 42)
        The seed to use for the random number generator

    args:
        The arguments passed to the program
        
    Returns:
    --------
        train_loader, val_loader, test_loader, train_set, val_set, test_set
    """
    pl.seed_everything(seed)
    np.random.seed(seed)
    
    if args.grayscale:
        grayscale = transforms.Grayscale(3)
    else:
        grayscale = transforms.RandomGrayscale(p=0.2)

    tensor_normalize = [
        transforms.ToTensor(),
        transforms.Normalize(
            [0.5818, 0.5700, 0.5632], 
            [0.1417, 0.1431, 0.1367]
        )
    ]

    augmentations = [
        # Gaussian Noise
        transforms.GaussianBlur(3),
        # Defocus Blur
        DefocusBlur(seed=seed, prob=0.1),
        # Bright ness
        transforms.ColorJitter(brightness=0.1),
        # Camera
        JpegCompression(seed=seed, prob=0.1),
        # Random Rotation
        transforms.RandomRotation(15),
        # Radom Grayscale
        grayscale
    ]

    if args.resize == 1:
        train_transform = transforms.Compose([
            *augmentations,
            transforms.Resize((args.height, args.width)),
            *tensor_normalize
        ])
        if args.grayscale:
            test_transform = transforms.Compose([
                transforms.Grayscale(3),
                transforms.Resize((args.height, args.width)),
                *tensor_normalize
            ])
        else:
            test_transform = transforms.Compose([
                transforms.Resize((args.height, args.width)),
                *tensor_normalize
            ])
    else:
        train_transform = transforms.Compose([
            *augmentations,
            FixedHeightResize(args.height), 
            FixedWidthPad(args.width),
            *tensor_normalize
        ])
        if args.grayscale:
            test_transform = transforms.Compose([
                transforms.Grayscale(3),
                FixedHeightResize(args.height), 
                FixedWidthPad(args.width),
                *tensor_normalize
            ])
        else:
            test_transform = transforms.Compose([
                FixedHeightResize(args.height), 
                FixedWidthPad(args.width),
                *tensor_normalize
            ])
    
    if args.model_name in ['crnn', 'cnnctc']:
        dataset = HandWritttenDataset
        collate = collate_fn_ctc
    else:
        dataset = HandWrittenDatasetV2
        collate = collate_fn

    train_dataset = dataset(
        TRAIN_DIR, LABEL_FILE,
        name='train', transform=train_transform
    )
    val_dataset = dataset(
        TRAIN_DIR, LABEL_FILE,
        name='train', transform=test_transform
    )
    test_dataset = dataset(
        PUBLIC_TEST_DIR,
        name='public_test', transform=test_transform
    )

    if args.train:
        form_inds = np.arange(0, 51000)
        wild_inds = np.arange(51000, 99000)
        gan_inds = np.arange(99000, 103000)
        np.random.shuffle(form_inds)
        np.random.shuffle(wild_inds)
        # Use GAN data only for training
        train_inds = np.concatenate([
            form_inds[5100:],
            wild_inds[4800:],
            gan_inds
        ])
        val_inds = np.concatenate([
            form_inds[:5100],
            wild_inds[:4800]
        ])
        if args.num_samples > 0:
            train_inds = np.random.choice(train_inds, args.num_samples, replace=False)

        train_set = Subset(train_dataset, train_inds)
        val_set = Subset(val_dataset, val_inds)
    else:
        print('Using all training data for training')
        train_set = train_dataset
        if args.num_samples > 0:
            train_set = Subset(train_dataset, np.random.choice(len(train_dataset), args.num_samples, replace=False))
        val_set = None

    train_loader = DataLoader(
        train_set, batch_size=batch_size,
        shuffle=True, drop_last=True, collate_fn=collate, 
        pin_memory=True, num_workers=2
    )
    if args.train:
        val_loader = DataLoader(
            val_set, batch_size=batch_size, shuffle=False, collate_fn=collate,
            pin_memory=True, num_workers=2
        )
    else:
        val_loader = None

    test_loader = DataLoader(
        test_dataset, batch_size=batch_size, shuffle=False, pin_memory=True, num_workers=2
    )

    return train_loader, val_loader, test_loader, train_dataset, val_dataset, test_dataset


class FixedHeightResize:
    def __init__(self, size):
        self.size = size
        
    def __call__(self, img):
        w, h = img.size
        aspect_ratio = float(h) / float(w)
        new_w = math.ceil(self.size / aspect_ratio)
        return F.resize(img, (self.size, new_w))
    
    
# Pad to fixed width
class FixedWidthPad:
    def __init__(self, size):
        self.size = size
        
    def __call__(self, img):
        w, h = img.size
        pad = self.size - w
        pad_left = pad // 2
        pad_right = pad - pad_left
        return F.pad(img, (pad_left, 0, pad_right, 0), 0, 'constant')