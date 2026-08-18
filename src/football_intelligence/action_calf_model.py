"""CALF ContextAwareModel (isolated).

This is a self-contained port of SoccerNet's CALF ``inference/model.py`` under
the repository's Apache-2.0 license. It is imported lazily only by
``CalfActionSpotter`` so the core package never depends on torch. Consumes
SoccerNet features of shape ``(1, 1, chunk_frames, 512)`` and returns a
segmentation map and a spotting tensor ``(1, num_detections, 2 + num_classes)``.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class ContextAwareModel(nn.Module):
    def __init__(
        self,
        *,
        input_size: int = 512,
        num_classes: int = 17,
        chunk_size: int = 240,
        dim_capsule: int = 16,
        receptive_field: int = 80,
        num_detections: int = 15,
    ) -> None:
        super().__init__()
        self.input_size = input_size
        self.num_classes = num_classes
        self.dim_capsule = dim_capsule
        self.receptive_field = receptive_field
        self.num_detections = num_detections
        self.chunk_size = chunk_size

        self.pyramid_size_1 = int(np.ceil(receptive_field / 7))
        self.pyramid_size_2 = int(np.ceil(receptive_field / 3))
        self.pyramid_size_3 = int(np.ceil(receptive_field / 2))
        self.pyramid_size_4 = int(np.ceil(receptive_field))

        self.conv_1 = nn.Conv2d(1, 128, kernel_size=(1, input_size))
        self.conv_2 = nn.Conv2d(128, 32, kernel_size=(1, 1))

        self.pad_p_1 = nn.ZeroPad2d(
            (0, 0, (self.pyramid_size_1 - 1) // 2,
             self.pyramid_size_1 - 1 - (self.pyramid_size_1 - 1) // 2)
        )
        self.pad_p_2 = nn.ZeroPad2d(
            (0, 0, (self.pyramid_size_2 - 1) // 2,
             self.pyramid_size_2 - 1 - (self.pyramid_size_2 - 1) // 2)
        )
        self.pad_p_3 = nn.ZeroPad2d(
            (0, 0, (self.pyramid_size_3 - 1) // 2,
             self.pyramid_size_3 - 1 - (self.pyramid_size_3 - 1) // 2)
        )
        self.pad_p_4 = nn.ZeroPad2d(
            (0, 0, (self.pyramid_size_4 - 1) // 2,
             self.pyramid_size_4 - 1 - (self.pyramid_size_4 - 1) // 2)
        )
        self.conv_p_1 = nn.Conv2d(32, 8, kernel_size=(self.pyramid_size_1, 1))
        self.conv_p_2 = nn.Conv2d(32, 16, kernel_size=(self.pyramid_size_2, 1))
        self.conv_p_3 = nn.Conv2d(32, 32, kernel_size=(self.pyramid_size_3, 1))
        self.conv_p_4 = nn.Conv2d(32, 64, kernel_size=(self.pyramid_size_4, 1))

        kernel_seg_size = 3
        self.pad_seg = nn.ZeroPad2d(
            (0, 0, (kernel_seg_size - 1) // 2,
             kernel_seg_size - 1 - (kernel_seg_size - 1) // 2)
        )
        self.conv_seg = nn.Conv2d(
            152, dim_capsule * num_classes, kernel_size=(kernel_seg_size, 1)
        )
        self.batch_seg = nn.BatchNorm2d(
            num_features=chunk_size, momentum=0.01, eps=0.001
        )

        self.max_pool_spot = nn.MaxPool2d(kernel_size=(3, 1), stride=(2, 1))
        kernel_spot_size = 3
        self.pad_spot_1 = nn.ZeroPad2d(
            (0, 0, (kernel_spot_size - 1) // 2,
             kernel_spot_size - 1 - (kernel_spot_size - 1) // 2)
        )
        self.conv_spot_1 = nn.Conv2d(
            num_classes * (dim_capsule + 1), 32, kernel_size=(kernel_spot_size, 1)
        )
        self.max_pool_spot_1 = nn.MaxPool2d(kernel_size=(3, 1), stride=(2, 1))
        self.pad_spot_2 = nn.ZeroPad2d(
            (0, 0, (kernel_spot_size - 1) // 2,
             kernel_spot_size - 1 - (kernel_spot_size - 1) // 2)
        )
        self.conv_spot_2 = nn.Conv2d(32, 16, kernel_size=(kernel_spot_size, 1))
        self.max_pool_spot_2 = nn.MaxPool2d(kernel_size=(3, 1), stride=(2, 1))

        self.conv_conf = nn.Conv2d(
            16 * (chunk_size // 8 - 1), num_detections * 2, kernel_size=(1, 1)
        )
        self.conv_class = nn.Conv2d(
            16 * (chunk_size // 8 - 1), num_detections * num_classes, kernel_size=(1, 1)
        )
        self.softmax = nn.Softmax(dim=-1)

    def forward(self, inputs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        conv_1 = F.relu(self.conv_1(inputs))
        conv_2 = F.relu(self.conv_2(conv_1))

        conv_p_1 = F.relu(self.conv_p_1(self.pad_p_1(conv_2)))
        conv_p_2 = F.relu(self.conv_p_2(self.pad_p_2(conv_2)))
        conv_p_3 = F.relu(self.conv_p_3(self.pad_p_3(conv_2)))
        conv_p_4 = F.relu(self.conv_p_4(self.pad_p_4(conv_2)))

        concatenation = torch.cat((conv_2, conv_p_1, conv_p_2, conv_p_3, conv_p_4), 1)

        conv_seg = self.conv_seg(self.pad_seg(concatenation))
        conv_seg_permuted = conv_seg.permute(0, 2, 3, 1)
        conv_seg_reshaped = conv_seg_permuted.view(
            conv_seg_permuted.size()[0],
            conv_seg_permuted.size()[1],
            self.dim_capsule,
            self.num_classes,
        )
        conv_seg_norm = torch.sigmoid(self.batch_seg(conv_seg_reshaped))
        output_segmentation = torch.sqrt(
            torch.sum(torch.square(conv_seg_norm - 0.5), dim=2) * 4 / self.dim_capsule
        )

        output_segmentation_reverse = 1 - output_segmentation
        output_segmentation_reverse_reshaped = output_segmentation_reverse.unsqueeze(
            2
        ).permute(0, 3, 1, 2)
        concatenation_2 = torch.cat(
            (conv_seg, output_segmentation_reverse_reshaped), dim=1
        )

        conv_spot = self.max_pool_spot(F.relu(concatenation_2))
        conv_spot_1 = F.relu(self.conv_spot_1(self.pad_spot_1(conv_spot)))
        conv_spot_1_pooled = self.max_pool_spot_1(conv_spot_1)
        conv_spot_2 = F.relu(self.conv_spot_2(self.pad_spot_2(conv_spot_1_pooled)))
        conv_spot_2_pooled = self.max_pool_spot_2(conv_spot_2)

        spotting_reshaped = conv_spot_2_pooled.view(conv_spot_2_pooled.size()[0], -1, 1, 1)

        conf_pred = torch.sigmoid(
            self.conv_conf(spotting_reshaped).view(
                spotting_reshaped.shape[0], self.num_detections, 2
            )
        )
        conf_class = self.softmax(
            self.conv_class(spotting_reshaped).view(
                spotting_reshaped.shape[0], self.num_detections, self.num_classes
            )
        )
        output_spotting = torch.cat((conf_pred, conf_class), dim=-1)
        return output_segmentation, output_spotting
