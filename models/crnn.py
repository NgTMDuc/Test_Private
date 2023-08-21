import torch
import torch.nn as nn
import torch.nn.functional as F
import pytorch_lightning as pl

from .utils import initialize_weights
from .resnet import ResNet_FeatureExtractor
from .transformation import TPS_SpatialTransformerNetwork


class CRNN(nn.Module):
    """
    CNN + RNN + CTC
    """

    def __init__(self, img_channel, img_height, img_width, num_class,
                 map_to_seq_hidden=64, rnn_hidden=256, leaky_relu=False,
                 dropout=0.0, feature_extractor='vgg', stn_on=False
                 ):
        """
        Arguments:
        ----------

        img_channel: int
            Number of channels of input image

        img_height: int
            Height of input image

        img_width: int
            Width of input image

        num_class: int
            Number of classes

        map_to_seq_hidden: int
            Number of hidden units of the linear layer mapping CNN feature to sequence

        rnn_hidden: int
            Number of hidden units of RNN

        leaky_relu: bool
            Whether to use leaky relu or relu as activation function

        dropout: float
            Dropout rate

        feature_extractor: str (default: 'vgg')
            Name of feature extractor, either 'vgg' or 'resnet'

        stn_on: bool (default: False)
            Whether to use STN
        """
        
        super(CRNN, self).__init__()

        if stn_on:
            self.tps = TPS_SpatialTransformerNetwork(
                20, (img_height, img_width), (img_height, img_width), 3
            )
        else:
            self.tps = nn.Identity()

        self.feature_extractor = feature_extractor

        if feature_extractor == 'vgg':
            self.cnn, (output_channel, output_height, output_width) = \
                self.vgg_backbone(img_channel, img_height, img_width, leaky_relu)
        elif feature_extractor == 'resnet':
            self.cnn, (output_channel, output_height, output_width) = \
                self.resnet_backbone(img_channel, img_height, img_width)
            initialize_weights(self.cnn)
        else:
            raise ValueError(f'Unsupported feature extractor: {feature_extractor}')

        self.map_to_seq = nn.Linear(output_channel * output_height, map_to_seq_hidden)

        self.rnn1 = nn.LSTM(map_to_seq_hidden, rnn_hidden, bidirectional=True)
        self.rnn2 = nn.LSTM(2 * rnn_hidden, rnn_hidden, bidirectional=True)

        self.dense = nn.Linear(2 * rnn_hidden, num_class)

        if dropout > 0:
            self.dropout1 = nn.Dropout(dropout)
            # self.dropout2 = nn.Dropout(dropout)
        self.drop_out = dropout


    def forward(self, images):
        # shape of images: (batch, channel, height, width)
        images = self.tps(images)
        conv = self.cnn(images)
        if self.drop_out > 0:
            conv = self.dropout1(conv)

        batch, channel, height, width = conv.size()
        conv = conv.view(batch, channel * height, width)
        conv = conv.permute(2, 0, 1)  # (width, batch, feature)
        seq = self.map_to_seq(conv)

        # # Add dropout layer if specified
        # if self.drop_out > 0:
        #     seq = self.dropout1(seq)

        recurrent, _ = self.rnn1(seq)
        recurrent, _ = self.rnn2(recurrent)

        # # Add dropout layer if specified
        # if self.drop_out > 0:
        #     recurrent = self.dropout2(recurrent)

        output = self.dense(recurrent)
        return output  # shape: (seq_len, batch, num_class)
    

    def vgg_backbone(self, img_channel, img_height, img_width, leaky_relu):
        assert img_height % 16 == 0
        assert img_width % 4 == 0

        channels = [img_channel, 64, 128, 256, 256, 512, 512, 512]
        kernel_sizes = [3, 3, 3, 3, 3, 3, 2]
        strides = [1, 1, 1, 1, 1, 1, 1]
        paddings = [1, 1, 1, 1, 1, 1, 0]

        cnn = nn.Sequential()

        def conv_relu(i, batch_norm=False):
            # shape of input: (batch, input_channel, height, width)
            input_channel = channels[i]
            output_channel = channels[i+1]

            cnn.add_module(
                f'conv{i}',
                nn.Conv2d(input_channel, output_channel, kernel_sizes[i], strides[i], paddings[i])
            )

            if batch_norm:
                cnn.add_module(f'batchnorm{i}', nn.BatchNorm2d(output_channel))

            relu = nn.LeakyReLU(0.2, inplace=True) if leaky_relu else nn.ReLU(inplace=True)
            cnn.add_module(f'relu{i}', relu)

        # size of image: (channel, height, width) = (img_channel, img_height, img_width)
        conv_relu(0)
        cnn.add_module('pooling0', nn.MaxPool2d(kernel_size=2, stride=2))
        # (64, img_height // 2, img_width // 2)

        conv_relu(1)
        cnn.add_module('pooling1', nn.MaxPool2d(kernel_size=2, stride=2))
        # (128, img_height // 4, img_width // 4)

        conv_relu(2)
        conv_relu(3)
        cnn.add_module(
            'pooling2',
            nn.MaxPool2d(kernel_size=(2, 1))
        )  # (256, img_height // 8, img_width // 4)

        conv_relu(4, batch_norm=True)
        conv_relu(5, batch_norm=True)
        cnn.add_module(
            'pooling3',
            nn.MaxPool2d(kernel_size=(2, 1))
        )  # (512, img_height // 16, img_width // 4)

        conv_relu(6)  # (512, img_height // 16 - 1, img_width // 4 - 1)

        output_channel, output_height, output_width = \
            channels[-1], img_height // 16 - 1, img_width // 4 - 1
        return cnn, (output_channel, output_height, output_width)
    

    def resnet_backbone(self, img_channel, img_height, img_width):
        output_channel = 512
        output_height = img_height // 16 - 1
        output_width = img_width // 4 + 1
        cnn = ResNet_FeatureExtractor(img_channel, 512)
        return cnn, (output_channel, output_height, output_width)