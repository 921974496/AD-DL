from torch import nn
import torch
from copy import deepcopy

from tools.deep_learning.models.modules import PadMaxPool3d, CropMaxUnpool3d, Flatten, Reshape
from tools.deep_learning.models import load_model


def transfer_learning(model, split, target_path, source_path=None, gpu=False):

    if source_path is not None:
        if transfer_from_autoencoder(source_path):
            print("A pretrained autoencoder is loaded at path %s" % transfer_learning)
            model_path = apply_autoencoder_weights(model, source_path, target_path, split)
            model, _ = load_model(model, model_path, gpu, filename='model_pretrained.pth.tar')

        else:
            print("A pretrained model is loaded at path %s" % source_path)
            model_path = apply_pretrained_network_weights(model, source_path, target_path, split)
            model, _ = load_model(model, model_path, gpu, filename='model_pretrained.pth.tar')

    return model


def transfer_from_autoencoder(experiment_path):
    import os

    # Find specific folders in experiment directory
    folds = os.listdir(os.path.join(experiment_path, "best_model_dir"))
    models = os.listdir(os.path.join(experiment_path, "best_model_dir", folds[0]))
    if models == ["ConvAutoencoder"]:
        return True
    return False


class Decoder(nn.Module):

    def __init__(self, model=None):
        from copy import deepcopy
        super(Decoder, self).__init__()

        self.level = 0

        if model is not None:
            self.encoder = deepcopy(model.features)
            self.decoder = self.construct_inv_layers(model)

            for i, layer in enumerate(self.encoder):
                if isinstance(layer, PadMaxPool3d):
                    self.encoder[i].set_new_return()
                elif isinstance(layer, nn.MaxPool3d):
                    self.encoder[i].return_indices = True
        else:
            self.encoder = nn.Sequential()
            self.decoder = nn.Sequential()

    def __len__(self):
        return len(self.encoder)

    def forward(self, x):

        indices_list = []
        pad_list = []
        for layer in self.encoder:
            if isinstance(layer, PadMaxPool3d):
                x, indices, pad = layer(x)
                indices_list.append(indices)
                pad_list.append(pad)
            elif isinstance(layer, nn.MaxPool3d):
                x, indices = layer(x)
                indices_list.append(indices)
            else:
                x = layer(x)

        for layer in self.decoder:
            if isinstance(layer, CropMaxUnpool3d):
                x = layer(x, indices_list.pop(), pad_list.pop())
            elif isinstance(layer, nn.MaxUnpool3d):
                x = layer(x, indices_list.pop())
            else:
                x = layer(x)

        return x

    def construct_inv_layers(self, model):
        inv_layers = []
        for i, layer in enumerate(self.encoder):
            if isinstance(layer, nn.Conv3d):
                inv_layers.append(nn.ConvTranspose3d(layer.out_channels, layer.in_channels, layer.kernel_size,
                                                     stride=layer.stride, padding=layer.padding))
                self.level += 1
            elif isinstance(layer, PadMaxPool3d):
                inv_layers.append(CropMaxUnpool3d(layer.kernel_size, stride=layer.stride))
            elif isinstance(layer, nn.MaxPool3d):
                inv_layers.append(nn.MaxUnpool3d(layer.kernel_size, stride=layer.stride))
            elif isinstance(layer, nn.Linear):
                inv_layers.append(nn.Linear(layer.out_features, layer.in_features))
            elif isinstance(layer, Flatten):
                inv_layers.append(Reshape(model.flattened_shape))
            elif isinstance(layer, nn.LeakyReLU):
                inv_layers.append(nn.LeakyReLU(negative_slope=1 / layer.negative_slope))
            elif i == len(self.encoder) - 1 and isinstance(layer, nn.BatchNorm3d):
                pass
            else:
                inv_layers.append(deepcopy(layer))
        inv_layers = self.replace_relu(inv_layers)
        inv_layers.reverse()
        return nn.Sequential(*inv_layers)

    @staticmethod
    def replace_relu(inv_layers):
        idx_relu, idx_conv = -1, -1
        for idx, layer in enumerate(inv_layers):
            if isinstance(layer, nn.ConvTranspose3d):
                idx_conv = idx
            elif isinstance(layer, nn.ReLU) or isinstance(layer, nn.LeakyReLU):
                idx_relu = idx

            if idx_conv != -1 and idx_relu != -1:
                inv_layers[idx_relu], inv_layers[idx_conv] = inv_layers[idx_conv], inv_layers[idx_relu]
                idx_conv, idx_relu = -1, -1

        # Check if number of features of batch normalization layers is still correct
        for idx, layer in enumerate(inv_layers):
            if isinstance(layer, nn.BatchNorm3d):
                conv = inv_layers[idx + 1]
                inv_layers[idx] = nn.BatchNorm3d(conv.out_channels)

        return inv_layers


def apply_autoencoder_weights(model, source_path, target_path, split, difference=0):
    from copy import deepcopy
    import os
    from classifiers.three_d_cnn.subject_level.classification import save_checkpoint, check_and_clean

    decoder = Decoder(model)
    model_path = os.path.join(source_path, "best_model_dir", "fold_" + str(split), "ConvAutoencoder",
                              "best_loss", "model_best.pth.tar")

    # TODO this is not secure, must add a check to find out the difference
    initialize_other_autoencoder(decoder, model_path, difference=difference)

    model.features = deepcopy(decoder.encoder)
    for layer in model.features:
        if isinstance(layer, PadMaxPool3d):
            layer.set_new_return(False, False)

    pretraining_path = os.path.join(target_path, 'best_model_dir', 'fold_' + str(split),
                                    'ConvAutoencoder', 'Encoder')
    check_and_clean(pretraining_path)

    save_checkpoint({'model': model.state_dict(),
                     'epoch': -1,
                     'path': model_path},
                    False, False,
                    pretraining_path,
                    filename='model_pretrained.pth.tar')

    return pretraining_path


def apply_pretrained_network_weights(model, source_path, target_path, split):
    import os
    import torch

    from classifiers.three_d_cnn.subject_level.classification import save_checkpoint, check_and_clean

    model_path = os.path.join(source_path, "best_model_dir", "fold_" + str(split), "CNN",
                              "best_loss", "model_best.pth.tar")
    results = torch.load(model_path)
    model.load_state_dict(results['model'])

    pretraining_path = os.path.join(target_path, 'best_model_dir', 'fold_' + str(split), 'CNN')
    check_and_clean(pretraining_path)

    save_checkpoint({'model': model.state_dict(),
                     'epoch': -1,
                     'path': model_path},
                    False, False,
                    pretraining_path,
                    filename='model_pretrained.pth.tar')

    return pretraining_path


def initialize_other_autoencoder(decoder, pretrained_autoencoder_path, difference=0):

    result_dict = torch.load(pretrained_autoencoder_path)
    parameters_dict = result_dict['model']
    module_length = int(len(decoder) / decoder.level)
    difference = difference * module_length

    for key in parameters_dict.keys():
        section, number, spec = key.split('.')
        number = int(number)
        if section == 'encoder' and number < len(decoder.encoder):
            data_ptr = eval('decoder.' + section + '[number].' + spec + '.data')
            data_ptr = parameters_dict[key]
        elif section == 'decoder':
            # Deeper autoencoder
            if difference >= 0:
                data_ptr = eval('decoder.' + section + '[number + difference].' + spec + '.data')
                data_ptr = parameters_dict[key]
            # More shallow autoencoder
            elif difference < 0 and number < len(decoder.decoder):
                data_ptr = eval('decoder.' + section + '[number].' + spec + '.data')
                new_key = '.'.join(['decoder', str(number + difference), spec])
                data_ptr = parameters_dict[new_key]

    return decoder
