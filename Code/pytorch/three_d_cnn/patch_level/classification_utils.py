import shutil
import torch
import pandas as pd
import numpy as np
import os, math
from os import path
from torch.utils.data import Dataset
from sklearn.model_selection import StratifiedShuffleSplit
import warnings
import torch.nn.functional as F

__author__ = "Junhao Wen"
__copyright__ = "Copyright 2018 The Aramis Lab Team"
__credits__ = ["Junhao Wen"]
__license__ = "See LICENSE.txt file"
__version__ = "0.1.0"
__maintainer__ = "Junhao Wen"
__email__ = "junhao.wen89@gmail.com"
__status__ = "Development"


#################################
#### AutoEncoder
#################################

def greedy_layer_wise_learning(model, train_loader, valid_loader, criterion, gpu, writer_train, writer_valid, options):
    """
    This aims to do greedy layer wise learning for autoencoder
    :param model:
    :param train_loader:
    :param valid_loader:
    :param criterion:
    :param gpu:
    :param writer_train:
    :param writer_valid:
    :param results_path:
    :param options:
    :return:
    """
    from os import path
    from model import AutoEncoder
    from copy import deepcopy

    if not isinstance(model, AutoEncoder):
        ae = AutoEncoder(model)

    level = 0
    first_layers = extract_first_layers(ae, level)
    auto_encoder = extract_ae(ae, level)

    while len(auto_encoder) > 0:
        print('Cell learning level %i' % level)
        level_path = path.join(options.output_dir, 'level-' + str(level))
        # Create the method to train with first layers
        ae_training(auto_encoder, first_layers, train_loader, valid_loader, criterion, gpu, level_path, options)
        best_ae, _ = load_model(auto_encoder, level_path)

        # Copy the weights of best_ae in decoder encoder and decoder layers
        set_weights(ae, best_ae, level)

        # Prepare next iteration
        level += 1
        first_layers = extract_first_layers(ae, level)
        auto_encoder = extract_ae(ae, level)

    # if options.add_sigmoid:
    #     if isinstance(ae.decoder[-1], torch.nn.ReLU):
    #         ae.decoder = torch.nn.Sequential(*list(ae.decoder)[:-1])
    #         ae.decoder.add_module("sigmoid", torch.nn.Sigmoid())

    ae_finetuning(ae, train_loader, valid_loader, criterion, gpu, options.output_dir, options)

    # Updating and setting weights of the convolutional layers
    best_decoder, best_epoch = load_model(ae, options.output_dir)
    if not isinstance(model, AutoEncoder):
        model.features = deepcopy(best_decoder.encoder)
        save_checkpoint({'model': model.state_dict(),
                         'epoch': best_epoch},
                        False,
                        os.path.join(options.output_dir),
                        'model_pretrained.pth.tar')

    # if options.visualization:
    #     visualize_ae(best_decoder, train_loader, os.path.join(options.output_dir, "train"), gpu)
    #     visualize_ae(best_decoder, valid_loader, os.path.join(options.output_dir, "valid"), gpu)

    return model

def ae_training(auto_encoder, first_layers, train_loader, valid_loader, criterion, gpu, results_path, options):
    from os import path

    if not path.exists(results_path):
        os.makedirs(results_path)

    filename = os.path.join(results_path, 'training.tsv')
    columns = ['epoch', 'iteration', 'loss_train', 'mean_loss_train', 'loss_valid', 'mean_loss_valid']
    results_df = pd.DataFrame(columns=columns)
    with open(filename, 'w') as f:
        results_df.to_csv(f, index=False, sep='\t')

    auto_encoder.train()
    first_layers.eval()
    print(first_layers)
    print(auto_encoder)
    optimizer = eval("torch.optim." + options.optimizer)(filter(lambda x: x.requires_grad, auto_encoder.parameters()),
                                                         options.transfer_learning_rate)

    if gpu:
        auto_encoder.cuda()

    # Initialize variables
    best_loss_valid = np.inf
    print("Beginning training")
    for epoch in range(options.transfer_learning_epochs):
        print("At %d-th epoch." % epoch)

        auto_encoder.zero_grad()
        evaluation_flag = True
        step_flag = True
        last_check_point_i = 0
        for i, data in enumerate(train_loader):
            if gpu:
                imgs = data['image'].cuda()
            else:
                imgs = data['image']

            hidden = first_layers(imgs)
            train_output = auto_encoder(hidden)
            loss = criterion(train_output, hidden)
            loss.backward()

            # writer_train.add_scalar('training_loss', loss.item() / len(data), i + epoch * len(train_loader.dataset))

            if (i+1) % options.accumulation_steps == 0:
                step_flag = False
                optimizer.step()
                optimizer.zero_grad()

                # Evaluate the decoder only when no gradients are accumulated
                if (i+1) % options.evaluation_steps == 0:
                    evaluation_flag = False
                    print('Iteration %d' % i)
                    loss_train = test_ae(auto_encoder, train_loader, gpu, criterion, first_layers=first_layers)
                    mean_loss_train = loss_train / (len(train_loader) * train_loader.dataset.size)
                    loss_valid = test_ae(auto_encoder, valid_loader, gpu, criterion, first_layers=first_layers)
                    mean_loss_valid = loss_valid / (len(valid_loader) * valid_loader.dataset.size)
                    auto_encoder.train()
                    print("Scan level validation loss is %f at the end of iteration %d" % (loss_valid, i))

                    row = np.array([epoch, i, loss_train, mean_loss_train, loss_valid, mean_loss_valid]).reshape(1, -1)
                    row_df = pd.DataFrame(row, columns=columns)
                    with open(filename, 'a') as f:
                        row_df.to_csv(f, header=False, index=False, sep='\t')

            del imgs

        # If no step has been performed, raise Exception
        if step_flag:
            raise Exception('The model has not been updated once in the epoch. The accumulation step may be too large.')

        # If no evaluation has been performed, warn the user
        if evaluation_flag:
            warnings.warn('Your evaluation steps are too big compared to the size of the dataset.'
                          'The model is evaluated only once at the end of the epoch')

        # Always test the results and save them once at the end of the epoch
        if last_check_point_i != i:
            print('Last checkpoint at the end of the epoch %d' % epoch)
            loss_train = test_ae(auto_encoder, train_loader, gpu, criterion, first_layers=first_layers)
            mean_loss_train = loss_train / (len(train_loader) * train_loader.dataset.size)
            loss_valid = test_ae(auto_encoder, valid_loader, gpu, criterion, first_layers=first_layers)
            mean_loss_valid = loss_valid / (len(valid_loader) * valid_loader.dataset.size)
            auto_encoder.train()
            print("Scan level validation loss is %f at the end of iteration %d" % (loss_valid, i))

            row = np.array([epoch, i, loss_train, mean_loss_train, loss_valid, mean_loss_valid]).reshape(1, -1)
            row_df = pd.DataFrame(row, columns=columns)
            with open(filename, 'a') as f:
                row_df.to_csv(f, header=False, index=False, sep='\t')

            is_best = loss_valid < best_loss_valid
            # Save only if is best to avoid performance deterioration
            if is_best:
                best_loss_valid = loss_valid
                save_checkpoint({'model': auto_encoder.state_dict(),
                                 'iteration': i,
                                 'epoch': epoch,
                                 'loss_valid': loss_valid},
                                is_best,
                                results_path)

def ae_finetuning(decoder, train_loader, valid_loader, criterion, gpu, results_path, options):
    from os import path

    if not path.exists(results_path):
        os.makedirs(results_path)
    filename = os.path.join(results_path, 'training.tsv')

    columns = ['epoch', 'iteration', 'loss_train', 'mean_loss_train', 'loss_valid', 'mean_loss_valid']
    results_df = pd.DataFrame(columns=columns)
    with open(filename, 'w') as f:
        results_df.to_csv(f, index=False, sep='\t')

    decoder.train()
    optimizer = eval("torch.optim." + options.optimizer)(filter(lambda x: x.requires_grad, decoder.parameters()),
                                                         options.transfer_learning_rate)
    first_visu = True
    print(decoder)

    if gpu:
        decoder.cuda()

    # Initialize variables
    best_loss_valid = np.inf
    print("Beginning training")
    for epoch in range(options.transfer_learning_epochs):
        print("At %d-th epoch." % epoch)

        decoder.zero_grad()
        evaluation_flag = True
        step_flag = True
        last_check_point_i = 0
        for i, data in enumerate(train_loader):
            if gpu:
                imgs = data['image'].cuda()
            else:
                imgs = data['image']

            train_output = decoder(imgs)
            loss = criterion(train_output, imgs)
            loss.backward()

            del imgs, train_output

            # writer_train.add_scalar('training_loss', loss.item() / len(data), i + epoch * len(train_loader.dataset))

            if (i+1) % options.accumulation_steps == 0:
                step_flag = False
                optimizer.step()
                optimizer.zero_grad()

                # Evaluate the decoder only when no gradients are accumulated
                if (i+1) % options.evaluation_steps == 0:
                    evaluation_flag = False
                    print('Iteration %d' % i)
                    loss_train = test_ae(decoder, train_loader, gpu, criterion)
                    mean_loss_train = loss_train / (len(train_loader) * train_loader.batch_size)
                    loss_valid = test_ae(decoder, valid_loader, gpu, criterion)
                    mean_loss_valid = loss_valid / (len(valid_loader) * valid_loader.batch_size)
                    decoder.train()
                    print("Scan level validation loss is %f at the end of iteration %d" % (loss_valid, i))
                    row = np.array([epoch, i, loss_train, mean_loss_train, loss_valid, mean_loss_valid]).reshape(1, -1)
                    row_df = pd.DataFrame(row, columns=columns)
                    with open(filename, 'a') as f:
                        row_df.to_csv(f, header=False, index=False, sep='\t')

        # If no step has been performed, raise Exception
        if step_flag:
            raise Exception('The model has not been updated once in the epoch. The accumulation step may be too large.')

        # If no evaluation has been performed, warn the user
        if evaluation_flag:
            warnings.warn('Your evaluation steps are too big compared to the size of the dataset.'
                          'The model is evaluated only once at the end of the epoch')

        # Always test the results and save them once at the end of the epoch
        if last_check_point_i != i:
            print('Last checkpoint at the end of the epoch %d' % epoch)
            loss_train = test_ae(decoder, train_loader, gpu, criterion)
            mean_loss_train = loss_train / (len(train_loader) * train_loader.batch_size)
            loss_valid = test_ae(decoder, valid_loader, gpu, criterion)
            mean_loss_valid = loss_valid / (len(valid_loader) * valid_loader.batch_size)
            decoder.train()
            print("Scan level validation loss is %f at the end of iteration %d" % (loss_valid, i))

            row = np.array([epoch, i, loss_train, mean_loss_train, loss_valid, mean_loss_valid]).reshape(1, -1)
            row_df = pd.DataFrame(row, columns=columns)
            with open(filename, 'a') as f:
                row_df.to_csv(f, header=False, index=False, sep='\t')

            is_best = loss_valid < best_loss_valid
            # Save only if is best to avoid performance deterioration
            if is_best:
                best_loss_valid = loss_valid
                save_checkpoint({'model': decoder.state_dict(),
                                 'iteration': i,
                                 'epoch': epoch,
                                 'loss_valid': loss_valid},
                                is_best,
                                results_path)
            # Save optimizer state_dict to be able to reload
            save_checkpoint({'optimizer': optimizer.state_dict(),
                             'epoch': epoch,
                             'name': options.optimizer,
                             },
                            False,
                            results_path,
                            filename='optimizer.pth.tar')

        if epoch % 10 == 0:
            visualize_subject(decoder, train_loader, results_path, epoch, options, first_visu)
            first_visu = False

def set_weights(decoder, auto_encoder, level):
    import torch.nn as nn

    n_conv = 0
    i_ae = 0

    for i, layer in enumerate(decoder.encoder):
        if isinstance(layer, nn.Conv3d):
            n_conv += 1

        if n_conv == level + 1:
            decoder.encoder[i] = auto_encoder.encoder[i_ae]
            # Do BatchNorm layers are not used in decoder
            if not isinstance(layer, nn.BatchNorm3d):
                decoder.decoder[len(decoder) - (i+1)] = auto_encoder.decoder[len(auto_encoder) - (i_ae+1)]
            i_ae += 1

    return decoder

def visualize_subject(decoder, dataloader, results_path, epoch, options, first_time=False):
    from os import path
    import nibabel as nib

    visualization_path = path.join(results_path, 'iterative_visualization')

    if not path.exists(visualization_path):
        os.makedirs(visualization_path)

    set_df = dataloader.dataset.df
    subject = set_df.loc[0, 'participant_id']
    session = set_df.loc[0, 'session_id']
    image_path = path.join(options.input_dir, 'subjects', subject, session,
                           't1', 'preprocessing_dl',
                            subject + '_' + session + '_space-MNI_res-1x1x1.nii.gz')

    input_nii = nib.load(image_path)
    input_np = input_nii.get_data()
    input_pt = torch.from_numpy(input_np).unsqueeze(0).unsqueeze(0).float()
    if options.minmaxnormalization:
        transform = MinMaxNormalization()
        input_pt = transform(input_pt)

    if options.gpu:
        input_pt = input_pt.cuda()

    output_pt = decoder(input_pt)

    if options.gpu:
        output_pt = output_pt.cpu()

    output_np = output_pt.detach().numpy()[0][0]
    output_nii = nib.Nifti1Image(output_np, affine=input_nii.affine)

    if first_time:
        nib.save(input_nii, path.join(visualization_path, 'input.nii'))

    nib.save(output_nii, path.join(visualization_path, 'epoch-' + str(epoch) + '.nii'))


def test_ae(model, dataloader, use_cuda, criterion, first_layers=None):
    """
    Computes the loss of the model

    :param model: the network (subclass of nn.Module)
    :param dataloader: a DataLoader wrapping a dataset
    :param use_cuda: if True a gpu is used
    :return: loss of the model (float)
    """
    model.eval()

    total_loss = 0
    for i, data in enumerate(dataloader, 0):
        if use_cuda:
            inputs = data['image'].cuda()
        else:
            inputs = data['image']

        if first_layers is not None:
            hidden = first_layers(inputs)
        else:
            hidden = inputs
        outputs = model(hidden)
        loss = criterion(outputs, hidden)
        total_loss += loss.item()

        del inputs, outputs, loss

    return total_loss

class MinMaxNormalization(object):
    """Normalizes a tensor between 0 and 1"""

    def __call__(self, image):
        return (image - image.min()) / (image.max() - image.min())


def apply_autoencoder_weights(model, pretrained_autoencoder_path, model_path, difference=0):
    from copy import deepcopy
    from os import path
    import os
    from model import AutoEncoder


    decoder = AutoEncoder(model)
    initialize_other_autoencoder(decoder, pretrained_autoencoder_path, model_path, difference=difference)

    model.features = deepcopy(decoder.encoder)
    if not path.exists(path.join(model_path, 'pretraining')):
        os.makedirs(path.join(model_path, "pretraining"))

    save_checkpoint({'model': model.state_dict(),
                     'epoch': -1,
                     'path': pretrained_autoencoder_path},
                    False,
                    path.join(model_path, "pretraining"),
                    'model_pretrained.pth.tar')

def load_model(model, checkpoint_dir, filename='model_best.pth.tar'):
    from copy import deepcopy

    best_model = deepcopy(model)
    param_dict = torch.load(os.path.join(checkpoint_dir, filename))
    best_model.load_state_dict(param_dict['model'])
    return best_model, param_dict['epoch']


def initialize_other_autoencoder(decoder, pretrained_autoencoder_path, model_path, difference=0):
    from os import path
    import os

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

    if not path.exists(path.join(model_path, 'pretraining')):
        os.makedirs(path.join(model_path, "pretraining"))

    save_checkpoint({'model': decoder.state_dict(),
                     'epoch': -1,
                     'path': pretrained_autoencoder_path},
                    False,
                    path.join(model_path, "pretraining"),
                    'model_pretrained.pth.tar')
    return decoder

def extract_first_layers(ae, level):
    import torch.nn as nn
    from copy import deepcopy
    from modules import PadMaxPool3d

    n_conv = 0
    first_layers = nn.Sequential()

    for i, layer in enumerate(ae.encoder):
        if isinstance(layer, nn.Conv3d):
            n_conv += 1

        if n_conv < level + 1:
            layer_copy = deepcopy(layer)
            layer_copy.requires_grad = False
            if isinstance(layer, PadMaxPool3d):
                layer_copy.set_new_return(False, False)

            first_layers.add_module(str(i), layer_copy)
        else:
            break

    return first_layers

def extract_ae(ae, level):
    import torch.nn as nn
    from model import AutoEncoder

    n_conv = 0
    output_ae = AutoEncoder()
    inverse_layers = []

    for i, layer in enumerate(ae.encoder):
        if isinstance(layer, nn.Conv3d):
            n_conv += 1

        if n_conv == level + 1:
            output_ae.encoder.add_module(str(len(output_ae.encoder)), layer)
            inverse_layers.append(ae.decoder[len(ae.decoder) - (i + 1)])

        elif n_conv > level + 1:
            break

    inverse_layers.reverse()
    output_ae.decoder = nn.Sequential(*inverse_layers)
    return output_ae


def replace_relu(inv_layers):
    import torch.nn as nn
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


###############################
### Hao's code
###############################

def train(model, data_loader, use_cuda, loss_func, optimizer, writer, epoch_i, model_mode="train", global_steps=0):
    """
    This is the function to train, validate or test the model, depending on the model_mode parameter.
    :param model:
    :param data_loader:
    :param use_cuda:
    :param loss_func:
    :param optimizer:
    :param writer:
    :param epoch_i:
    :return:
    """
    # main training loop
    acc = 0.0
    loss = 0.0

    subjects = []
    y_ground = []
    y_hat = []
    print("Start for %s!" % model_mode)
    if model_mode == "train":
        model.train() ## set the model to training mode
        print('The number of batches in this sampler based on the batch size: %s' % str(len(data_loader)))
        for i, batch_data in enumerate(data_loader):
            if use_cuda:
                imgs, labels = batch_data['image'].cuda(), batch_data['label'].cuda()
            else:
                imgs, labels = batch_data['image'], batch_data['label']

            ## add the participant_id + session_id
            image_ids = batch_data['image_id']
            subjects.extend(image_ids)

            gound_truth_list = labels.data.cpu().numpy().tolist()
            y_ground.extend(gound_truth_list)

            print('The group true label is %s' % (str(labels)))
            output = model(imgs)

            _, predict = output.topk(1)
            predict_list = predict.data.cpu().numpy().tolist()
            y_hat.extend([item for sublist in predict_list for item in sublist])
            if model_mode == "train" or model_mode == 'valid':
                print("output.device: " + str(output.device))
                print("labels.device: " + str(labels.device))
                print("The predicted label is: " + str(output))
                loss_batch = loss_func(output, labels)
            correct_this_batch = (predict.squeeze(1) == labels).sum().float()
            # To monitor the training process using tensorboard, we only display the training loss and accuracy, the other performance metrics, such
            # as balanced accuracy, will be saved in the tsv file.
            accuracy = float(correct_this_batch) / len(labels)
            acc += accuracy
            loss += loss_batch.item()

            print("For batch %d, training loss is : %f" % (i, loss_batch.item()))
            print("For batch %d, training accuracy is : %f" % (i, accuracy))

            writer.add_scalar('classification accuracy', accuracy, i + epoch_i * len(data_loader))
            writer.add_scalar('loss', loss_batch, i + epoch_i * len(data_loader))

            # Unlike tensorflow, in Pytorch, we need to manully zero the graident before each backpropagation step, becase Pytorch accumulates the gradients
            # on subsequent backward passes. The initial designing for this is convenient for training RNNs.
            optimizer.zero_grad()
            loss_batch.backward()
            optimizer.step()

            ## update the global steps
            global_steps = i + epoch_i * len(data_loader)

            # delete the temporal varibles taking the GPU memory
            # del imgs, labels
            del imgs, labels, output, predict, gound_truth_list, correct_this_batch, loss_batch
            # Releases all unoccupied cached memory
            torch.cuda.empty_cache()

        accuracy_batch_mean = acc / len(data_loader)
        loss_batch_mean = loss / len(data_loader)
        del loss_batch_mean
        torch.cuda.empty_cache()

    elif model_mode == "valid":
        model.eval() ## set the model to evaluation mode
        torch.cuda.empty_cache()
        with torch.no_grad():
            ## torch.no_grad() needs to be set, otherwise the accumulation of gradients would explose the GPU memory.
            print('The number of batches in this sampler based on the batch size: %s' % str(len(data_loader)))
            for i, batch_data in enumerate(data_loader):
                if use_cuda:
                    imgs, labels = batch_data['image'].cuda(), batch_data['label'].cuda()
                else:
                    imgs, labels = batch_data['image'], batch_data['label']

                ## add the participant_id + session_id
                image_ids = batch_data['image_id']
                subjects.extend(image_ids)

                gound_truth_list = labels.data.cpu().numpy().tolist()
                y_ground.extend(gound_truth_list)

                print('The group true label is %s' % (str(labels)))
                output = model(imgs)

                _, predict = output.topk(1)
                predict_list = predict.data.cpu().numpy().tolist()
                y_hat.extend([item for sublist in predict_list for item in sublist])
                print("output.device: " + str(output.device))
                print("labels.device: " + str(labels.device))
                print("The predicted label is: " + str(output))
                loss_batch = loss_func(output, labels)
                correct_this_batch = (predict.squeeze(1) == labels).sum().float()
                # To monitor the training process using tensorboard, we only display the training loss and accuracy, the other performance metrics, such
                # as balanced accuracy, will be saved in the tsv file.
                accuracy = float(correct_this_batch) / len(labels)
                acc += accuracy
                loss += loss_batch.item()
                print("For batch %d, validation accuracy is : %f" % (i, accuracy))

                # delete the temporal varibles taking the GPU memory
                # del imgs, labels
                del imgs, labels, output, predict, gound_truth_list, correct_this_batch, loss_batch
                # Releases all unoccupied cached memory
                torch.cuda.empty_cache()

            accuracy_batch_mean = acc / len(data_loader)
            loss_batch_mean = loss / len(data_loader)

            writer.add_scalar('classification accuracy', accuracy_batch_mean, global_steps)
            writer.add_scalar('loss', loss_batch_mean, global_steps)

            del loss_batch_mean
            torch.cuda.empty_cache()

    return subjects, y_ground, y_hat, accuracy_batch_mean, global_steps


def train_sparse_ae(autoencoder, data_loader, use_cuda, loss_func, optimizer, writer, epoch_i, options):
    """
    This trains the sparse autoencoder.
    :param autoencoder:
    :param data_loader:
    :param use_cuda:
    :param loss_func:
    :param optimizer:
    :param writer:
    :param epoch_i:
    :param global_steps:
    :return:
    """
    print("Start training for sparse autoencoder!")
    # Releases all unoccupied cached memory
    torch.cuda.empty_cache()
    epoch_loss = 0
    sparsity = 0.05
    beta = 3
    print('The number of batches in this sampler based on the batch size: %s' % str(len(data_loader)))
    for i, batch_data in enumerate(data_loader):
        if use_cuda:
            imgs = batch_data['image'].cuda()
        else:
            imgs = batch_data['image']

        ## check if the patch contains no information, which means the patch is at the edge fo the MRI and contains NAN
        if torch.sum(torch.isnan(imgs.view(1, -1))):
            del imgs
            pass

        else:
            decoded, encoded = autoencoder(imgs)
            imgs_flatten = imgs.view(imgs.shape[0], options.patch_size * options.patch_size * options.patch_size)
            loss1 = loss_func(decoded, imgs_flatten) / options.batch_size
            if use_cuda:
                rho = (torch.ones([1, encoded.shape[1]]) * sparsity).cuda()
                rho_hat = torch.sum(encoded, dim=0, keepdim=True).cuda()
            else:
                rho = torch.ones([1, encoded.shape[1]]) * sparsity ## this value should be near to 0.
                rho_hat = torch.sum(encoded, dim=0, keepdim=True)
            ## the sparsity loss
            loss2 = kl_divergence(rho, rho_hat) * beta
            if np.sum(np.isnan(imgs_flatten.detach().numpy())):
                raise Exception('Stop, this is wrong! imgs_flatten')
            if np.sum(np.isnan(decoded.detach().numpy())):
                raise Exception('Stop, this is wrong! decoded')
            if np.sum(np.isnan(rho.detach().numpy())):
                raise Exception('Stop, this is wrong! rho')
            if np.sum(np.isnan(rho_hat.detach().numpy())):
                raise Exception('Stop, this is wrong! rho_hat')
            # kl_div_loss(mean_activitaion, sparsity)
            loss = loss1 + beta * loss2 ## beta indicates the importance of the sparsity loss
            epoch_loss += loss
            print("For batch %d, training loss is : %f" % (i, loss.item()))

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            ## save loss into tensorboardX
            writer.add_scalar('loss', loss, i + epoch_i * len(data_loader))
            ## save memory
            del imgs, decoded, loss, loss1, loss2, encoded, rho, imgs_flatten, rho_hat

    return epoch_loss


def results_to_tsvs(output_dir, iteration, subject_list, y_truth, y_hat, mode='train'):
    """
    This is a function to trace all subject during training, test and validation, and calculate the performances with different metrics into tsv files.
    :param output_dir:
    :param iteration:
    :param subject_list:
    :param y_truth:
    :param y_hat:
    :return:
    """

    # check if the folder exist
    iteration_dir = os.path.join(output_dir, 'performances', 'iteration-' + str(iteration))
    if not os.path.exists(iteration_dir):
        os.makedirs(iteration_dir)
    iteration_subjects_df = pd.DataFrame({'iteration': iteration,
                                                'y': y_truth,
                                                'y_hat': y_hat,
                                                'subject': subject_list})
    iteration_subjects_df.to_csv(os.path.join(iteration_dir, mode + '_subjects.tsv'), index=False, sep='\t', encoding='utf-8')

    results = evaluate_prediction(np.asarray(y_truth), np.asarray(y_hat))
    del results['confusion_matrix']
    pd.DataFrame(results, index=[0]).to_csv(os.path.join(iteration_dir, mode + '_result.tsv'), index=False, sep='\t', encoding='utf-8')

    return iteration_subjects_df, pd.DataFrame(results, index=[0])

def evaluate_prediction(y, y_hat):

    true_positive = 0.0
    true_negative = 0.0
    false_positive = 0.0
    false_negative = 0.0

    tp = []
    tn = []
    fp = []
    fn = []

    for i in range(len(y)):
        if y[i] == 1:
            if y_hat[i] == 1:
                true_positive += 1
                tp.append(i)
            else:
                false_negative += 1
                fn.append(i)
        else:  # -1
            if y_hat[i] == 0:
                true_negative += 1
                tn.append(i)
            else:
                false_positive += 1
                fp.append(i)

    accuracy = (true_positive + true_negative) / (true_positive + true_negative + false_positive + false_negative)

    if (true_positive + false_negative) != 0:
        sensitivity = true_positive / (true_positive + false_negative)
    else:
        sensitivity = 0.0

    if (false_positive + true_negative) != 0:
        specificity = true_negative / (false_positive + true_negative)
    else:
        specificity = 0.0

    if (true_positive + false_positive) != 0:
        ppv = true_positive / (true_positive + false_positive)
    else:
        ppv = 0.0

    if (true_negative + false_negative) != 0:
        npv = true_negative / (true_negative + false_negative)
    else:
        npv = 0.0

    balanced_accuracy = (sensitivity + specificity) / 2

    results = {'accuracy': accuracy,
               'balanced_accuracy': balanced_accuracy,
               'sensitivity': sensitivity,
               'specificity': specificity,
               'ppv': ppv,
               'npv': npv,
               'confusion_matrix': {'tp': len(tp), 'tn': len(tn), 'fp': len(fp), 'fn': len(fn)}
               }

    return results


class MRIDataset_patch(Dataset):
    """labeled Faces in the Wild dataset."""

    def __init__(self, caps_directory, data_file, patch_size, stride_size, transformations=None, data_type='from_patch'):
        """
        Args:
            caps_directory (string): Directory of all the images.
            data_file (string): File name of the train/test split file.
            transformations (callable, optional): Optional transformations to be applied on a sample.

        """
        self.caps_directory = caps_directory
        self.transformations = transformations
        self.diagnosis_code = {'CN': 0, 'AD': 1, 'sMCI': 0, 'pMCI': 1, 'MCI': 1}
        self.patch_size = patch_size
        self.stride_size = stride_size
        self.data_type = data_type

        # Check the format of the tsv file here
        self.df = pd.read_csv(data_file, sep='\t')
        if ('diagnosis' not in list(self.df.columns.values)) or ('session_id' not in list(self.df.columns.values)) or \
           ('participant_id' not in list(self.df.columns.values)):
            raise Exception("the data file is not in the correct format."
                            "Columns should include ['participant_id', 'session_id', 'diagnosis']")
        participant_list = list(self.df['participant_id'])
        session_list = list(self.df['session_id'])
        label_list = list(self.df['diagnosis'])

        ## dynamically calculate the number of patches from each MRI based on the parameters of patch_size & stride_size:
        ## Question posted on: https://discuss.pytorch.org/t/how-to-extract-smaller-image-patches-3d/16837/9
        patch_dims = [math.floor((169 - patch_size) / stride_size + 1), math.floor((208 - patch_size) / stride_size + 1), math.floor((179 - patch_size) / stride_size + 1)]
        self.patchs_per_patient = int(patch_dims[0] * patch_dims[1] * patch_dims[2])
        self.patch_participant_list = [ele for ele in participant_list for _ in range(self.patchs_per_patient)]
        self.patch_session_list = [ele for ele in session_list for _ in range(self.patchs_per_patient)]
        self.patch_label_list = [ele for ele in label_list for _ in range(self.patchs_per_patient)]

    def __len__(self):
        return len(self.patch_participant_list)

    def __getitem__(self, idx):
        img_name = self.patch_participant_list[idx]
        sess_name = self.patch_session_list[idx]
        img_label = self.patch_label_list[idx]
        ## image without intensity normalization
        label = self.diagnosis_code[img_label]
        index_patch = idx % self.patchs_per_patient

        if self.data_type == 'from_MRI':
            image_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1', 'preprocessing_dl', img_name + '_' + sess_name + '_space-MNI_res-1x1x1.pt')
            image = torch.load(image_path)
            ### extract the patch from MRI based on a specific size
            patch = extract_patch_from_mri(image, index_patch, self.patch_size, self.stride_size, self.patchs_per_patient)
        else:
            patch_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1',
                                      'preprocessing_dl',
                                      img_name + '_' + sess_name + '_space-MNI_res-1x1x1_patchsize-' + str(self.patch_size) + '_stride-' + str(self.stride_size) + '_patch-' + str(
                                          index_patch) + '.pt')
            patch = torch.load(patch_path)

        # check if the patch has NAN value
        if torch.isnan(patch).any() == True:	
            print("Double check, this patch has Nan value: %s" % str(img_name + '_' + sess_name + str(index_patch)))
            patch[torch.isnan(patch)] = 0

        if self.transformations:
            patch = self.transformations(patch)

        sample = {'image_id': img_name + '_' + sess_name, 'image': patch, 'label': label}

        return sample

    def session_restriction(self, session):
        """
            Allows to generate a new MRIDataset_patch using some specific sessions only (mostly used for evaluation of test)

            :param session: (str) the session wanted. Must be 'all' or 'ses-MXX'
            :return: (DataFrame) the dataset with the wanted sessions
            """
        from copy import copy

        data_output = copy(self)
        if session == "all":
            return data_output
        else:
            df_session = self.df[self.df.session_id == session]
            df_session.reset_index(drop=True, inplace=True)
            data_output.df = df_session
            if len(data_output) == 0:
                raise Exception("The session %s doesn't exist for any of the subjects in the test data" % session)
            return data_output


def subject_diagnosis_df(subject_session_df):
    """
    Creates a DataFrame with only one occurence of each subject and the most early diagnosis
    Some subjects may not have the baseline diagnosis (ses-M00 doesn't exist)

    :param subject_session_df: (DataFrame) a DataFrame with columns containing 'participant_id', 'session_id', 'diagnosis'
    :return: DataFrame with the same columns as the input
    """
    temp_df = subject_session_df.set_index(['participant_id', 'session_id'])
    subjects_df = pd.DataFrame(columns=subject_session_df.columns)
    for subject, subject_df in temp_df.groupby(level=0):
        session_nb_list = [int(session[5::]) for _, session in subject_df.index.values]
        session_nb_list.sort()
        session_baseline_nb = session_nb_list[0]
        if session_baseline_nb < 10:
            session_baseline = 'ses-M0' + str(session_baseline_nb)
        else:
            session_baseline = 'ses-M' + str(session_baseline_nb)
        row_baseline = list(subject_df.loc[(subject, session_baseline)])
        row_baseline.insert(0, subject)
        row_baseline.insert(1, session_baseline)
        row_baseline = np.array(row_baseline).reshape(1, len(row_baseline))
        row_df = pd.DataFrame(row_baseline, columns=subject_session_df.columns)
        subjects_df = subjects_df.append(row_df)

    subjects_df.reset_index(inplace=True, drop=True)
    return subjects_df


def multiple_time_points(df, subset_df):
    """
    Returns a DataFrame with all the time points of each subject

    :param df: (DataFrame) the reference containing all the time points of all subjects.
    :param subset_df: (DataFrame) the DataFrame containing the subset of subjects.
    :return: mtp_df (DataFrame) a DataFrame with the time points of the subjects of subset_df
    """
    mtp_df = pd.DataFrame(columns=df.columns)
    temp_df = df.set_index('participant_id')
    for idx in subset_df.index.values:
        subject = subset_df.loc[idx, 'participant_id']
        subject_df = temp_df.loc[subject]
        if isinstance(subject_df, pd.Series):
            subject_id = subject_df.name
            row = list(subject_df.values)
            row.insert(0, subject_id)
            subject_df = pd.DataFrame(np.array(row).reshape(1, len(row)), columns=df.columns)
            mtp_df = mtp_df.append(subject_df)
        else:
            mtp_df = mtp_df.append(subject_df.reset_index())

    mtp_df.reset_index(inplace=True, drop=True)
    return mtp_df

def split_subjects_to_tsv(diagnoses_tsv, val_size=0.15, random_state=None):
    """
    Write the tsv files corresponding to the train/val/test splits of all folds

    :param diagnoses_tsv: (str) path to the tsv file with diagnoses
    :param val_size: (float) proportion of the train set being used for validation
    :return: None
    """

    df = pd.read_csv(diagnoses_tsv, sep='\t')
    if 'diagnosis' not in list(df.columns.values):
        raise Exception('Diagnoses file is not in the correct format.')
    # Here we reduce the DataFrame to have only one diagnosis per subject (multiple time points case)
    diagnosis_df = subject_diagnosis_df(df)
    diagnoses_list = list(diagnosis_df.diagnosis)
    unique = list(set(diagnoses_list))
    y = np.array([unique.index(x) for x in diagnoses_list])  # There is one label per diagnosis depending on the order

    sets_dir = path.join(path.dirname(diagnoses_tsv),
                         path.basename(diagnoses_tsv).split('.')[0],
                         'val_size-' + str(val_size))
    if not path.exists(sets_dir):
        os.makedirs(sets_dir)

    # split the train data into training and validation set
    skf_2 = StratifiedShuffleSplit(n_splits=1, test_size=val_size, random_state=random_state)
    indices = next(skf_2.split(np.zeros(len(y)), y))
    train_ind, valid_ind = indices

    df_sub_valid = diagnosis_df.iloc[valid_ind]
    df_sub_train = diagnosis_df.iloc[train_ind]
    df_valid = multiple_time_points(df, df_sub_valid)
    df_train = multiple_time_points(df, df_sub_train)

    df_valid.to_csv(path.join(sets_dir, 'valid.tsv'), sep='\t', index=False)
    df_train.to_csv(path.join(sets_dir, 'train.tsv'), sep='\t', index=False)

def load_split(diagnoses_tsv, val_size=0.15, random_state=None):
    """
    Returns the paths of the TSV files for each set

    :param diagnoses_tsv: (str) path to the tsv file with diagnoses
    :param val_size: (float) the proportion of the training set used for validation
    :return: 3 Strings
        training_tsv
        valid_tsv
    """
    sets_dir = path.join(path.dirname(diagnoses_tsv),
                         path.basename(diagnoses_tsv).split('.')[0],
                         'val_size-' + str(val_size))

    training_tsv = path.join(sets_dir, 'train.tsv')
    valid_tsv = path.join(sets_dir, 'valid.tsv')

    if not path.exists(training_tsv) or not path.exists(valid_tsv):
        split_subjects_to_tsv(diagnoses_tsv, val_size, random_state=random_state)

        training_tsv = path.join(sets_dir, 'train.tsv')
        valid_tsv = path.join(sets_dir, 'valid.tsv')

    return training_tsv, valid_tsv

def load_split_autoencoder(train_val_path, diagnoses_list, baseline=True):
    """
    Creates a DataFrame for training and validation sets given the wanted diagnoses

    :param train_val_path: Path to the train / val decomposition
    :param diagnoses_list: list of diagnoses to select to construct the DataFrames
    :param baseline: bool choose to use baseline only instead of all data available
    :return:
        train_df DataFrame with training data
        valid_df DataFrame with validation data
    """
    train_df = pd.DataFrame()
    valid_df = pd.DataFrame()

    for diagnosis in diagnoses_list:

        if baseline:
            train_diagnosis_path = path.join(train_val_path, 'train', diagnosis + '_baseline.tsv')

        else:
            train_diagnosis_path = path.join(train_val_path, 'train', diagnosis + '.tsv')

        valid_diagnosis_path = path.join(train_val_path, 'validation', diagnosis + '_baseline.tsv')

        train_diagnosis_df = pd.read_csv(train_diagnosis_path, sep='\t')
        valid_diagnosis_df = pd.read_csv(valid_diagnosis_path, sep='\t')

        train_df = pd.concat([train_df, train_diagnosis_df])
        valid_df = pd.concat([valid_df, valid_diagnosis_df])

    train_df.reset_index(inplace=True, drop=True)
    valid_df.reset_index(inplace=True, drop=True)

    return train_df, valid_df



def extract_patch_from_mri(image_tensor, index_patch, patch_size, stride_size, patchs_per_patient):

    ## use pytorch tensor.upfold to crop the patch.
    patches_tensor = image_tensor.unfold(1, patch_size, stride_size).unfold(2, patch_size, stride_size).unfold(3, patch_size, stride_size).contiguous()
    # the dimension of patch_tensor should be [1, patch_num1, patch_num2, patch_num3, patch_size1, patch_size2, patch_size3]
    patches_tensor = patches_tensor.view(-1, patch_size, patch_size, patch_size)
    if patchs_per_patient != patches_tensor.shape[0]:
        raise Exception("Oops, the number of patches were not correctly calculated")

    extracted_patch = patches_tensor[index_patch, ...].unsqueeze_(0) ## add one dimension

    return extracted_patch


def check_and_clean(d):

  if os.path.exists(d):
      shutil.rmtree(d)
  os.mkdir(d)

def save_checkpoint(state, is_best, checkpoint_dir, filename='checkpoint.pth.tar'):
    """
    This is the function to save the best model during validation process
    :param state: the parameters that you wanna save
    :param is_best: if the performance is better than before
    :param checkpoint_dir:
    :param filename:
    :return:
    """
    import shutil, os
    if not os.path.exists(checkpoint_dir):
        os.makedirs(checkpoint_dir)
    torch.save(state, os.path.join(checkpoint_dir, filename))
    if is_best:
        shutil.copyfile(os.path.join(checkpoint_dir, filename),  os.path.join(checkpoint_dir, 'model_best.pth.tar'))

class NormalizeMinMax(object):
    def __init__(self):
        pass

    def __call__(self, tensor):
        if isinstance(tensor, torch.Tensor):
            ## normalize to [0, 1]
            tensor = (tensor - tensor.min()) / (tensor.max() - tensor.min())

            return tensor
        else:
            raise Exception('CustomNormalizedMinMax needs a torch tensor, but it is not given.')

def kl_divergence(p, q):
    '''
    This is the penalty term quantified by KL divergence.
    ref: http://ufldl.stanford.edu/wiki/index.php/Autoencoders_and_Sparsity
    :param p:
    :param q:
    :return:
    '''
    p = F.softmax(p)
    q = F.softmax(q)

    s1 = torch.sum(p * torch.log(p / q))
    s2 = torch.sum((1 - p) * torch.log((1 - p) / (1 - q)))

    return s1 + s2

def extract_slice_img(x):
    """
    This is to extrac a middle slice of the input patch or MRI to check the reconstruction quality
    :param x:
    :return:
    """
    slices = x[:, 0, x.shape[-1] // 2, ...].unsqueeze(1)
    return slices

def visualize_ae(ae, data, results_path):
    """
    To reconstruct one example patch and save it in nifti format for visualization
    :param ae:
    :param data: tensor, shape [1, 1, height, width, length]
    :param results_path:
    :return:
    """
    import nibabel as nib
    import os

    # set the model to be eval
    ae.eval()
    encoder, decoder = ae(data)
    reconstructed_nii = nib.Nifti1Image(decoder[0][0].cpu().detach().numpy(), np.eye(4))
    input_nii = nib.Nifti1Image(data[0][0].cpu().detach().numpy(), np.eye(4))
    nib.save(reconstructed_nii, os.path.join(results_path, 'example_patch_reconstructed.nii.gz'))
    nib.save(input_nii, os.path.join(results_path, 'example_patch_original.nii.gz'))
