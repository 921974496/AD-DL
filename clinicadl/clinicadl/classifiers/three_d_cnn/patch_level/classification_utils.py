import shutil
import torch
import pandas as pd
import numpy as np
import os, math
from os import path
from torch.utils.data import Dataset
from sklearn.model_selection import StratifiedShuffleSplit
import torch.nn.functional as F
from time import time
import tempfile

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

def greedy_layer_wise_learning(model, train_loader, valid_loader, criterion, writer_train, writer_valid, writer_train_ft, writer_valid_ft, options, fi):
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
        Return both the pretrained CNN for future use and also the stacked AEs
    """
    from os import path
    from model import AutoEncoder
    from copy import deepcopy
    ## if the model defined is not already construted to an AE, then we convert the CNN into an AE, keeping the same structure with original CNN
    if not isinstance(model, AutoEncoder):
        ae = AutoEncoder(model) ## Reconstruct all the AEs in one graph

    ## Here, to extract each AE for layer-wise training 
    level = 0
    former_layer = frozen_weight_layer_wise(ae, level) ## extract the former AE
    auto_encoder_layer = extract_ae_layer_wise(ae, level)

    while len(auto_encoder_layer) > 0:
        print('Layer-wise training for %d -th AE' % level)
        # Create the method to train with first layers
        best_model_former_layer_path,  optimizer_train = ae_training(auto_encoder_layer, former_layer, train_loader, valid_loader, criterion, options, writer_train, writer_valid, level, fi)
        del optimizer_train
        best_ae, _ = load_model_from_chcekpoint(auto_encoder_layer, best_model_former_layer_path)

        # Copy the weights of best_ae in decoder encoder and decoder layers
        ae = stack_layer_wise_ae(ae, best_ae, level)

        # Prepare next AE
        level += 1
        former_layer = frozen_weight_layer_wise(ae, level) ## to frozen the former layer of AE and extract the encoder part of this AE
        auto_encoder_layer = extract_ae_layer_wise(ae, level) ## extract the next AE

    ae_finetuning(ae, train_loader, valid_loader, criterion, writer_train_ft, writer_valid_ft, options, fi)

    # Updating and setting weights of the convolutional layers
    best_autodecoder, best_epoch = load_model_from_chcekpoint(ae, path.join(options.output_dir, 'best_model_dir', "fold_" + str(fi), 'ConvAutoencoder', 'fine_tune', 'AutoEncoder', 'model_best.pth.tar'))

    ## save only the Encoders too
    model.features = deepcopy(best_autodecoder.encoder)
    save_checkpoint({'model': model.state_dict(),
                     'epoch': best_epoch},
                    False,
                    os.path.join(options.output_dir, 'best_model_dir', "fold_" + str(fi), 'ConvAutoencoder', 'fine_tune', 'Encoder'),
                    'model_best_encoder.pth.tar')

    return model, best_autodecoder

def stacked_ae_learning(model, train_loader, valid_loader, criterion, writer_train, writer_valid, options, fi):
    """
    This aims to train the stacked AEs together for autoencoder
    :param model:
    :param train_loader:
    :param valid_loader:
    :param criterion:
    :param writer_train:
    :param writer_valid:
    :param options:
    :return:
        Return both the pretrained CNN for future use and also the stacked AEs
    """
    from os import path
    from tools.deep_learning.models import AutoEncoder
    from copy import deepcopy
    ## if the model defined is not already construted to an AE, then we convert the CNN into an AE, keeping the same structure with original CNN
    if not isinstance(model, AutoEncoder):
        ae = AutoEncoder(model) ## Reconstruct all the AEs in one graph

    ae_finetuning(ae, train_loader, valid_loader, criterion, writer_train, writer_valid, options, fi)

    # Updating and setting weights of the convolutional layers
    best_autodecoder, best_epoch = load_model_from_chcekpoint(ae, path.join(options.output_dir, 'best_model_dir', "fold_" + str(fi), 'ConvAutoencoder', 'fine_tune', 'AutoEncoder'), 'model_best.pth.tar')

    del ae

    # ## save the encoder part of the AEs, the best AEs has been saved in the ae_finetuning part
    model.features = deepcopy(best_autodecoder.encoder)
    save_checkpoint({'model': model.state_dict(),
                     'epoch': best_epoch},
                    False,
                    os.path.join(options.output_dir, 'best_model_dir', "fold_" + str(fi), 'ConvAutoencoder', 'fine_tune', 'Encoder'),
                    'model_best_encoder.pth.tar')

    del best_epoch

    return model, best_autodecoder

def ae_training(auto_encoder, former_layer, train_loader, valid_loader, criterion, options, writer_train, writer_valid, level, fi, global_step=0):
    """
    This is the function to train the AEs in a greedy layer-wise way.
    :param auto_encoder:
    :param former_layer:
    :param train_loader:
    :param valid_loader:
    :param criterion:
    :param gpu:
    :param results_path:
    :param options:
    :return:
    """
    auto_encoder.train()
    former_layer.eval()
    print(former_layer)
    print(auto_encoder)
    optimizer_train = eval("torch.optim." + options.optimizer)(filter(lambda x: x.requires_grad, auto_encoder.parameters()),
                                                         options.learning_rate)

    if options.use_gpu:
        auto_encoder.cuda()

    # Initialize variables
    best_loss_valid = np.inf
    print("Beginning layer-wise training")
    for epoch in range(options.epochs_layer_wise):
        print("Layer-wise training at %d-th epoch." % epoch)

        auto_encoder.zero_grad()

        print('The number of batches in this sampler based on the batch size: %s' % str(len(train_loader)))
        tend = time()
        total_time = 0

        ## begin the training for each batch data
        for i, data in enumerate(train_loader):
            # torch.cuda.synchronize()
            t0 = time()
            total_time = total_time + t0 - tend

            # print("Loading available between batches of data by CPU using time: ", t0 - tend)

            if options.use_gpu:
                imgs = data['image'].cuda()
            else:
                imgs = data['image']
            # print("Device used: " + str(imgs.device))
            # torch.cuda.synchronize()
            # t1 = time()
            # total_time += t1 - t0
            # print("Loading data on GPU", t1 - t0)

            hidden = former_layer(imgs) ## output of encoder for former AE and input for the encoder of next AE
            train_output = auto_encoder(hidden)

            # torch.cuda.synchronize()
            # t2 = time()
            # print("Real time forward pass", t2 - t1)

            ## explicitly set the variable of criterion to be requires_grad=False
            hidden_requires_grad_no = hidden.detach()
            hidden_requires_grad_no.requires_grad = False

            loss_train = criterion(train_output, hidden_requires_grad_no)
            loss_train.backward()
            # torch.cuda.synchronize()
            # t3 = time()
            # print("Backward pass", t3 - t2)

            # moniter the training loss for each batch using tensorboardX
            writer_train.add_scalar('loss_layer-' + str(level), loss_train, i + epoch * len(train_loader))
            print("Training loss is %f for the -th batch %d" % (loss_train, i))

            optimizer_train.step()
            optimizer_train.zero_grad()

            del imgs, train_output, hidden, hidden_requires_grad_no, loss_train
            ## update the global steps
            global_step = i + epoch * len(train_loader)

            torch.cuda.empty_cache()
            # torch.cuda.synchronize()
            # t_temp = time()
            # print('Training the %d -th batch in total using  %f s:' % (i, t_temp -t0))

            tend = time()

        print('Mean time per batch (train):', total_time / len(train_loader))

        # Always test the results and save them once at the end of the epoch
        print('Layer-wise validation at the end of each epoch %d' % epoch)
        loss_valid = test_ae(auto_encoder, valid_loader, options, criterion, former_layer=former_layer)
        mean_loss_valid = loss_valid / (len(valid_loader))
        writer_valid.add_scalar('loss_layer-' + str(level), mean_loss_valid, global_step)
        print("Layer-wise mean validation loss is %f for the -th epoch %d" % (mean_loss_valid, global_step))

        ## reset the model to train mode after evaluation
        auto_encoder.train()

        best_model_path = os.path.join(options.output_dir, "best_model_dir", "fold_" + str(fi), "ConvAutoencoder", "layer-" + str(level))

        is_best = loss_valid < best_loss_valid
        # Save only if is best to avoid performance deterioration
        best_loss_valid = min(loss_valid, best_loss_valid)
        save_checkpoint({'model': auto_encoder.state_dict(),
                         'iteration': i,
                         'epoch': epoch,
                         'best_loss': mean_loss_valid},
                        is_best,
                        best_model_path)

    return best_model_path, optimizer_train

def ae_finetuning(auto_encoder_all, train_loader, valid_loader, criterion, writer_train_ft, writer_valid_ft, options, fi, global_step=0):
    """
    After training the AEs in a layer-wise way, we fine-tune the whole AEs
    :param auto_encoder:
    :param train_loader:
    :param valid_loader:
    :param criterion:
    :param gpu:
    :param results_path:
    :param options:
    :return:
    """

    auto_encoder_all.train()
    optimizer = eval("torch.optim." + options.optimizer)(filter(lambda x: x.requires_grad, auto_encoder_all.parameters()),
                                                         options.learning_rate)
    print(auto_encoder_all)

    if options.use_gpu:
        auto_encoder_all.cuda()

    # Initialize variables
    best_loss_valid = np.inf
    print("Beginning fine-tuning")

    print('The number of batches in this sampler based on the batch size: %s' % str(len(train_loader)))
    tend = time()
    total_time = 0

    for epoch in range(options.epochs_fine_tuning):
        print("Fine-tuning at %d-th epoch." % epoch)

        auto_encoder_all.zero_grad()

        for i, data in enumerate(train_loader):

            torch.cuda.synchronize()
            t0 = time()
            total_time = total_time + t0 - tend

            # print("Loading available between batches of data by CPU using time: ", t0 - tend)

            if options.use_gpu:
                imgs = data['image'].cuda()
            else:
                imgs = data['image']

            # print("Device used: " + str(imgs.device))
            # torch.cuda.synchronize()
            # t1 = time()
            # total_time += t1 - t0
            # print("Loading data on GPU", t1 - t0)

            train_output = auto_encoder_all(imgs)

            # torch.cuda.synchronize()
            # t2 = time()
            # print("Real time forward pass", t2 - t1)

            loss = criterion(train_output, imgs)
            loss.backward()

            # torch.cuda.synchronize()
            # t3 = time()
            # print("Backward pass", t3 - t2)

            # moniter the training loss for each batch using tensorboardX
            writer_train_ft.add_scalar('loss', loss, i + epoch * len(train_loader))
            print("Training loss is %f for the -th batch %d" % (loss, i))

            ## update the global steps
            global_step = i + epoch * len(train_loader)

            del imgs, train_output, loss

            optimizer.step()
            optimizer.zero_grad()

            torch.cuda.empty_cache()

            # torch.cuda.synchronize()
            # t_temp = time()
            # print('Training the %d -th batch in total using  %f s:' % (i, t_temp -t0))

            tend = time()

        print('Mean time per batch (train):', total_time / len(train_loader))

        # Always test the results and save them once at the end of the epoch
        print('Fine-tuning all AEs of validation at the end of the epoch %d' % epoch)
        loss_valid = test_ae(auto_encoder_all, valid_loader, options, criterion)
        mean_loss_valid = loss_valid / (len(valid_loader))
        writer_valid_ft.add_scalar('loss', mean_loss_valid, global_step)
        print("Fine-tuning mean validation loss is %f for the -th batch %d" % (mean_loss_valid, global_step))

        ## reset the model to train mode after evaluation
        auto_encoder_all.train()

        is_best_loss = loss_valid < best_loss_valid
        # Save best based on smallest loss
        best_loss_valid = min(loss_valid, best_loss_valid)
        save_checkpoint({'model': auto_encoder_all.state_dict(),
                         'iteration': i,
                         'epoch': epoch,
                         'best_loss': best_loss_valid},
                        is_best_loss,
                        os.path.join(options.output_dir, "best_model_dir", "fold_" + str(fi), "ConvAutoencoder", "fine_tune", "AutoEncoder"))

    del optimizer, auto_encoder_all


def test_ae(model, dataloader, options, criterion, former_layer=None):
    """
    Computes the loss of the model, either the loss of the layer-wise AE or all the AEs in a big graph one time.

    :param model: the network (subclass of nn.Module)
    :param dataloader: a DataLoader wrapping a dataset
    :param use_gpu: if True a gpu is used
    :return: loss of the model (float)
    """
    model.eval()

    total_loss = 0
    for i, data in enumerate(dataloader, 0):
        if options.use_gpu:
            inputs = data['image'].cuda()
        else:
            inputs = data['image']

        if former_layer is not None:
            hidden = former_layer(inputs)
        else:
            hidden = inputs
        outputs = model(hidden)
        ## explicitly set the variable of criterion to be requires_grad=False
        hidden_requires_grad_no = hidden.detach()
        hidden_requires_grad_no.requires_grad = False
        loss = criterion(outputs, hidden_requires_grad_no)
        total_loss += loss.item()
        torch.cuda.empty_cache()

        del inputs, outputs, loss

    return total_loss

def stack_layer_wise_ae(ae_all, ae_layer, level):
    """
    This is a function to transfer each layer-wise AE weights onto the graph-based AE, and then frozen the stacked AEs.
    :param ae_all:
    :param auto_encoder:
    :param level:
    :return:
    """
    import torch.nn as nn

    n_conv = 0
    i_ae = 0

    for i, layer in enumerate(ae_all.encoder):
        if isinstance(layer, nn.Conv3d): # if is convnet, adding one layer for ae
            n_conv += 1
        ## transfering the Layers in each block.
        if n_conv == level + 1:
            ae_all.encoder[i] = ae_layer.encoder[i_ae] ## transfer the layer-wise AE weight into the graph-wise AEs
            ae_all.decoder[len(ae_all) - (i+1)] = ae_layer.decoder[len(ae_layer) - (i_ae+1)]
            i_ae += 1

    return ae_all

def load_model_from_chcekpoint(model, checkpoint_dir, filename='model_best.pth.tar'):
    """
    Load the model based on the checkpoint file
    :param model:
    :param checkpoint_dir:
    :param filename:
    :return:
    """
    from copy import deepcopy

    best_model = deepcopy(model)
    param_dict = torch.load(os.path.join(checkpoint_dir, filename))
    best_model.load_state_dict(param_dict['model'])
    return best_model, param_dict['epoch']

def frozen_weight_layer_wise(ae_all, level):
    """
    This is to frozen the weight of the former AE
    :param ae_all:
    :param level:
    :return:
    """
    import torch.nn as nn
    from copy import deepcopy
    from modules import PadMaxPool3d

    n_conv = 0
    former_layer = nn.Sequential()

    for i, layer in enumerate(ae_all.encoder):
        if isinstance(layer, nn.Conv3d):
            n_conv += 1

        if n_conv < level + 1: ### frozen former layer weight
            layer_copy = deepcopy(layer)
            layer_copy.requires_grad = False
            if isinstance(layer, PadMaxPool3d):
                layer_copy.set_new_return(False, False)

            former_layer.add_module(str(i), layer_copy)
        else:
            break

    return former_layer

def extract_ae_layer_wise(ae, level):
    """
    This is to extract the layer-wise AE from the the graph-based AEs based on the argument level.
    :param ae:
    :param level:
    :return:
    """
    import torch.nn as nn
    from model import AutoEncoder

    n_conv = 0
    output_ae = AutoEncoder()
    decoder_layers = []

    for i, layer in enumerate(ae.encoder):
        if isinstance(layer, nn.Conv3d):
            n_conv += 1

        if n_conv == level + 1:
            output_ae.encoder.add_module(str(len(output_ae.encoder)), layer)
            decoder_layers.append(ae.decoder[len(ae.decoder) - (i + 1)])

        elif n_conv > level + 1:
            break

    decoder_layers.reverse()
    output_ae.decoder = nn.Sequential(*decoder_layers)
    return output_ae


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

def load_model_after_ae(model, checkpoint_dir, filename='checkpoint.pth.tar'):
    """
    This is to copy the weight of the pretrained AE to the current CNN
    :param model:
    :param checkpoint_dir:
    :param filename:
    :return:
    """
    from copy import deepcopy

    model_after_ae = deepcopy(model)
    model_dict = model_after_ae.state_dict()
    param_dict = torch.load(os.path.join(checkpoint_dir, filename))
    ae_pretrained_dict = param_dict['model']

    ## remove the classifier's weight, only take the AE
    for k in ae_pretrained_dict.keys():
        if 'classifier' not in k:
            pass
        else:
            del ae_pretrained_dict[k]

    model_dict.update(ae_pretrained_dict)
    model_after_ae.load_state_dict(model_dict)

    return model_after_ae, param_dict['epoch']

def load_model_after_cnn(model, checkpoint_dir, filename='checkpoint.pth.tar'):
    """

    :param model:
    :param checkpoint_dir:
    :param filename:
    :return:
    """
    from copy import deepcopy

    model.eval()
    model_updated = deepcopy(model)
    param_dict = torch.load(os.path.join(checkpoint_dir, filename))
    model_updated.load_state_dict(param_dict['model'])

    return model_updated, param_dict['epoch']


def load_model_from_log(model, optimizer, checkpoint_dir, filename='checkpoint.pth.tar'):
    """
    This is to load a saved model from the log folder
    :param model:
    :param checkpoint_dir:
    :param filename:
    :return:
    """
    from copy import deepcopy

    ## set the model to be eval mode, we explicitly think that the model was saved in eval mode, otherwise, it will affects the BN and dropout

    model.eval()
    model_updated = deepcopy(model)
    param_dict = torch.load(os.path.join(checkpoint_dir, filename))
    model_updated.load_state_dict(param_dict['model'])
    optimizer.load_state_dict(param_dict['optimizer'])

    return model_updated, optimizer, param_dict['global_step'], param_dict['epoch']


def train(model, data_loader, use_cuda, loss_func, optimizer, writer, epoch_i, iteration, model_mode="train", global_step=0):
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
    proba = []

    # ## accumulate the former batches of data
    # train_images = []
    # train_labels = []

    print("Start for %s!" % model_mode)
    if model_mode == "train":
        model.train() ## set the model to training mode
        print('The number of batches in this sampler based on the batch size: %s' % str(len(data_loader)))

        for i, batch_data in enumerate(data_loader):

            if use_cuda:
                imgs, labels = batch_data['image'].cuda(), batch_data['label'].cuda()
            else:
                imgs, labels = batch_data['image'], batch_data['label']

            # add the participant_id + session_id
            image_ids = batch_data['image_id']
            subjects.extend(image_ids)

            gound_truth_list = labels.data.cpu().numpy().tolist()
            y_ground.extend(gound_truth_list)

            print('The group true label is %s' % (str(labels)))
            output = model(imgs)

            _, predict = output.topk(1)
            predict_list = predict.data.cpu().numpy().tolist()
            predict_list = [item for sublist in predict_list for item in sublist]
            y_hat.extend(predict_list)

            print("The predicted label is: " + str(output))
            loss_batch = loss_func(output, labels)

            # adding the probability
            proba.extend(output.data.cpu().numpy().tolist())

            # calculate the balanced accuracy
            results = evaluate_prediction(gound_truth_list, predict_list)
            accuracy = results['balanced_accuracy']
            acc += accuracy
            loss += loss_batch.item()

            writer.add_scalar('classification accuracy', accuracy, global_step)
            writer.add_scalar('loss', loss_batch, global_step)

            print("For batch %d, training loss is : %f" % (i, loss_batch.item()))
            print("For batch %d, training accuracy is : %f" % (i, accuracy))
            optimizer.zero_grad()
            loss_batch.backward()
            optimizer.step()

            # update the global steps
            global_step = i + epoch_i * len(data_loader)

            # delete the temporary variables taking the GPU memory
            del imgs, labels, output, predict, gound_truth_list, loss_batch, accuracy, results
            torch.cuda.empty_cache()

        accuracy_batch_mean = acc / len(data_loader)
        loss_batch_mean = loss / len(data_loader)
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
                predict_list = [item for sublist in predict_list for item in sublist]
                y_hat.extend(predict_list)
                print("The predicted label is: " + str(output))
                loss_batch = loss_func(output, labels)

                # adding the probability
                proba.extend(output.data.cpu().numpy().tolist())

                # calculate the balanced accuracy
                results = evaluate_prediction(gound_truth_list, predict_list)
                accuracy = results['balanced_accuracy']

                loss += loss_batch.item()
                print("For batch %d, validation accuracy is : %f" % (i, accuracy))

                # delete the temporary variables taking the GPU memory
                del imgs, labels, output, predict, gound_truth_list, accuracy, loss_batch, results
                torch.cuda.empty_cache()

            # calculate the balanced accuracy
            results = soft_voting_subject_level(y_ground, y_hat, subjects, proba, iteration)
            accuracy_batch_mean = results['balanced_accuracy']
            loss_batch_mean = loss / len(data_loader)

            writer.add_scalar('classification accuracy', accuracy_batch_mean, epoch_i)
            writer.add_scalar('loss', loss_batch_mean, epoch_i)

            torch.cuda.empty_cache()

    return subjects, y_ground, y_hat, proba, accuracy_batch_mean, global_step, loss_batch_mean

def test(model, data_loader, options):
    """
    The function to evaluate the testing data for the trained classifiers
    :param model:
    :param test_loader:
    :param options.:
    :return:
    """

    subjects = []
    y_ground = []
    y_hat = []
    proba = []
    print("Start evaluate the model!")
    if options.use_gpu:
        model.cuda()

    model.eval()  ## set the model to evaluation mode
    torch.cuda.empty_cache()
    with torch.no_grad():
        ## torch.no_grad() needs to be set, otherwise the accumulation of gradients would explose the GPU memory.
        print('The number of batches in this sampler based on the batch size: %s' % str(len(data_loader)))
        for i, batch_data in enumerate(data_loader):
            if options.use_gpu:
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
            predict_list = [item for sublist in predict_list for item in sublist]
            y_hat.extend(predict_list)

            print("output.device: " + str(output.device))
            print("labels.device: " + str(labels.device))
            print("The predicted label is: " + str(output))

            ## adding the probability
            proba.extend(output.data.cpu().numpy().tolist())

            ## calculate the balanced accuracy
            results = evaluate_prediction(gound_truth_list, predict_list)
            accuracy = results['balanced_accuracy']
            print("For batch %d, test accuracy is : %f" % (i, accuracy))

            # delete the temporal varibles taking the GPU memory
            del imgs, labels, output, predict, gound_truth_list, accuracy, results
            # Releases all unoccupied cached memory
            torch.cuda.empty_cache()

        ## calculate the balanced accuracy
        results = evaluate_prediction(y_ground, y_hat)
        accuracy_batch_mean = results['balanced_accuracy']
        torch.cuda.empty_cache()

    return subjects, y_ground, y_hat, proba, accuracy_batch_mean


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
    :param global_step:
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

def hard_voting_to_tsvs(output_dir, iteration, subject_list, y_truth, y_hat, probas, mode='train', vote_mode='hard', patch_index=None):
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
    if patch_index == None:
        iteration_dir = os.path.join(output_dir, 'performances', 'fold_' + str(iteration))
    else:
        iteration_dir = os.path.join(output_dir, 'performances', 'fold_' + str(iteration), 'cnn-' + str(patch_index))


    if not os.path.exists(iteration_dir):
        os.makedirs(iteration_dir)

    performance_df = pd.DataFrame({'iteration': iteration,
                                                'y': y_truth,
                                                'y_hat': y_hat,
                                                'subject': subject_list,
                                                'probability': probas})

    ## save the patch level results
    performance_df.to_csv(os.path.join(iteration_dir, mode + '_patch_level_result-patch_index.tsv'), index=False, sep='\t', encoding='utf-8', columns=['subject', 'y', 'y_hat', 'probability', 'iteration'])

    ## save the sliece level different metrics
    results = evaluate_prediction(list(performance_df.y), [int(e) for e in list(performance_df.y_hat)]) ## Note, y_hat here is not int, is string
    del results['confusion_matrix']

    pd.DataFrame(results, index=[0]).to_csv(os.path.join(iteration_dir, mode + '_patch_level_metrics.tsv'), index=False, sep='\t', encoding='utf-8')

    ## calculate the subject-level performances based on the majority vote.
    # delete the patch number in the column of subject
    performance_df_subject = performance_df
    subject_df = performance_df_subject['subject']
    subject_series = subject_df.apply(extract_subject_name)
    subject_df_new = pd.DataFrame({'subject': subject_series.values})
    # replace the column in the dataframe
    performance_df_subject['subject'] = subject_df_new['subject'].values

    ## do hard majority vote
    df_y = performance_df_subject.groupby(['subject'], as_index=False).y.mean() # get the true label for each subject
    df_yhat = pd.DataFrame(columns=['subject', 'y_hat'])
    for subject, subject_df in performance_df_subject.groupby(['subject']):
        num_patch = len(subject_df.y_hat)
        patchs_predicted_as_one = subject_df.y_hat.sum()
        if patchs_predicted_as_one > num_patch / 2:
            label = 1
        else:
            label = 0
        row_array = np.array(list([subject, label])).reshape(1, 2)
        row_df = pd.DataFrame(row_array, columns=df_yhat.columns)
        df_yhat = df_yhat.append(row_df)

    # reset the index of df_yhat
    df_yhat.reset_index()
    result_df = pd.merge(df_y, df_yhat, on='subject')
    ## insert the column of iteration
    result_df['iteration'] = str(iteration)

    result_df.to_csv(os.path.join(iteration_dir, mode + '_subject_level_result_' + vote_mode + '_vote.tsv'), index=False, sep='\t', encoding='utf-8')

    results = evaluate_prediction(list(result_df.y), [int(e) for e in list(result_df.y_hat)]) ## Note, y_hat here is not int, is string
    del results['confusion_matrix']

    pd.DataFrame(results, index=[0]).to_csv(os.path.join(iteration_dir, mode + '_subject_level_metrics_' + vote_mode + '_vote.tsv'), index=False, sep='\t', encoding='utf-8')

def extract_subject_name(s):
    return s.split('_patch')[0]

def extract_patch_index(s):
    return s.split('_patch')[1]

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

def soft_voting_to_tsvs(output_dir, iteration, mode='test', vote_mode='soft'):
    """
    This is for soft voting for subject-level performances
    :param performance_df: the pandas dataframe, including columns: iteration, y, y_hat, subject, probability

    ref: S. Raschka. Python Machine Learning., 2015
    :return:
    """

    # check if the folder exist
    result_tsv = os.path.join(output_dir, 'performances', 'fold_' + str(iteration), 'test_patch_level_result-patch_index.tsv')

    performance_df = pd.io.parsers.read_csv(result_tsv, sep='\t')

    performance_df_subject = performance_df
    subject_df = performance_df_subject['subject']
    subject_series = subject_df.apply(extract_subject_name)
    patch_series = subject_df.apply(extract_patch_index)
    subject_df_new = pd.DataFrame({'subject': subject_series.values})
    patch_df_new = pd.DataFrame({'patch': patch_series.values})

    # replace the column in the dataframe
    performance_df_subject['subject'] = subject_df_new['subject'].values
    performance_df_subject['patch'] = patch_df_new['patch'].values

    ## selected the right classified subjects:
    right_classified_df = performance_df_subject[performance_df_subject['y_hat'] == performance_df_subject['y']]
    # right_classified_df = pd.DataFrame({'patch': right_classified_series['patch'].values})

    ## count the number of right classified patch for each patch index
    count_patchs_series = right_classified_df['patch'].value_counts(normalize=True)
    index_series = performance_df_subject['patch']
    weight_list = []
    for i in index_series:
        if i in count_patchs_series.index:
            weight = count_patchs_series[i]
        else:
            weight = 0
        weight_list.append(weight)

    weight_series = pd.Series(weight_list)
    ## add to the df
    performance_df_subject['weight'] = weight_series.values

    ## do soft majority vote
    ## y^ = arg max(sum(wj * pij))
    df_final = pd.DataFrame(columns=['subject', 'y', 'y_hat', 'iteration'])
    for subject, subject_df in performance_df_subject.groupby(['subject']):
        num_patch = len(subject_df.y_hat)
        p0_all = 0
        p1_all = 0
        for i in range(num_patch):
            ## reindex the subject_df.probability
            proba_series_reindex = subject_df.probability.reset_index()
            weight_series_reindex = subject_df.weight.reset_index()
            y_series_reindex = subject_df.y.reset_index()
            iteration_series_reindex = subject_df.iteration.reset_index()

            p0 = weight_series_reindex.weight[i] * eval(proba_series_reindex.probability[i])[0]
            p1 = weight_series_reindex.weight[i] * eval(proba_series_reindex.probability[i])[1]

            p0_all += p0
            p1_all += p1

            if i == 0:
                y = y_series_reindex.y[i]
                iteration = iteration_series_reindex.iteration[i]
        proba_list = [p0_all, p1_all]
        y_hat = proba_list.index(max(proba_list))


        row_array = np.array(list([subject, y, y_hat, iteration])).reshape(1, 4)
        row_df = pd.DataFrame(row_array, columns=['subject', 'y', 'y_hat', 'iteration'])
        df_final = df_final.append(row_df)

    df_final.to_csv(os.path.join(os.path.join(output_dir, 'performances', 'fold_' + str(iteration), mode + '_subject_level_result_' + vote_mode + '_vote.tsv')), index=False, sep='\t', encoding='utf-8')

    results = evaluate_prediction([int(e) for e in list(df_final.y)], [int(e) for e in list(df_final.y_hat)]) ## Note, y_hat here is not int, is string
    del results['confusion_matrix']

    pd.DataFrame(results, index=[0]).to_csv(os.path.join(output_dir, 'performances', 'fold_' + str(iteration), mode + '_subject_level_metrics_' + vote_mode + '_vote.tsv'), index=False, sep='\t', encoding='utf-8')

# output_dir = "/network/lustre/dtlake01/aramis/users/clinica/CLINICA_datasets/CAPS/Frontiers_DL/Experiments_results/AD_CN/ROI_based/Finished_exp/pytorch_AE_Conv_4_FC_2_bs4_lr_e5_only_finetuning_epoch200_baseline_hippocampus50_with_es_MedIA"
# iteration = 0
#
# soft_voting_to_tsvs(output_dir, iteration, mode='test', vote_mode='soft')

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
        if isinstance(data_file, str):
            self.df = pd.read_csv(data_file, sep='\t')
        elif isinstance(data_file, pd.DataFrame):
            self.df = data_file
        else:
            raise Exception('The argument datafile is not of correct type.')

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

        sample = {'image_id': img_name + '_' + sess_name + '_patch' + str(index_patch), 'image': patch, 'label': label}

        return sample

class MRIDataset_patch_hippocampus(Dataset):
    """Loading the left and right hippocampus ROIs."""

    def __init__(self, caps_directory, data_file, transformations=None):
        """
        Args:
            caps_directory (string): Directory of all the images.
            data_file (string): File name of the train/test split file.
            transformations (callable, optional): Optional transformations to be applied on a sample.

        """
        self.caps_directory = caps_directory
        self.transformations = transformations
        self.diagnosis_code = {'CN': 0, 'AD': 1, 'sMCI': 0, 'pMCI': 1, 'MCI': 1}

        # Check the format of the tsv file here
        if isinstance(data_file, str):
            self.df = pd.read_csv(data_file, sep='\t')
        elif isinstance(data_file, pd.DataFrame):
            self.df = data_file
        else:
            raise Exception('The argument datafile is not of correct type.')

        if ('diagnosis' not in list(self.df.columns.values)) or ('session_id' not in list(self.df.columns.values)) or \
           ('participant_id' not in list(self.df.columns.values)):
            raise Exception("the data file is not in the correct format."
                            "Columns should include ['participant_id', 'session_id', 'diagnosis']")
        participant_list = list(self.df['participant_id'])
        session_list = list(self.df['session_id'])
        label_list = list(self.df['diagnosis'])

        self.patchs_per_patient = 2
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
        ## odd is left hipp, even is right
        left_is_odd = idx % self.patchs_per_patient

        if left_is_odd == 1:
            patch_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1',
                                  'preprocessing_dl',
                                  img_name + '_' + sess_name + '_space-MNI_res-1x1x1_hippocampus_hemi-left.pt')
        else:
            patch_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1',
                                  'preprocessing_dl',
                                  img_name + '_' + sess_name + '_space-MNI_res-1x1x1_hippocampus_hemi-right.pt')

        patch = torch.load(patch_path)

        # check if the patch has NAN value
        if torch.isnan(patch).any() == True:
            print("Double check, this patch has Nan value: %s" % str(img_name + '_' + sess_name + str(left_is_odd)))
            patch[torch.isnan(patch)] = 0

        if self.transformations:
            patch = self.transformations(patch)

        ## TODO, maybe need to check if the patch only has background if the patch size is too small
        print("Mean value after normalization: %f" % torch.mean(patch))

        sample = {'image_id': img_name + '_' + sess_name + '_patch' + str(left_is_odd), 'image': patch, 'label': label}

        return sample


class MRIDataset_patch_by_index(Dataset):
    """Loading the left and right hippocampus ROIs."""

    def __init__(self, caps_directory, data_file, patch_size, stride_size, index_patch, transformations=None, data_type='from_patch'):
        """
        Args:
            caps_directory (string): Directory of all the images.
            data_file (string): File name of the train/test split file.
            transformations (callable, optional): Optional transformations to be applied on a sample.

        """
        self.caps_directory = caps_directory
        self.transformations = transformations
        self.index_patch = index_patch
        self.diagnosis_code = {'CN': 0, 'AD': 1, 'sMCI': 0, 'pMCI': 1, 'MCI': 1}
        self.patch_size = patch_size
        self.stride_size = stride_size
        self.data_type = data_type

        # Check the format of the tsv file here
        if isinstance(data_file, str):
            self.df = pd.read_csv(data_file, sep='\t')
        elif isinstance(data_file, pd.DataFrame):
            self.df = data_file
        else:
            raise Exception('The argument datafile is not of correct type.')

        if ('diagnosis' not in list(self.df.columns.values)) or ('session_id' not in list(self.df.columns.values)) or \
           ('participant_id' not in list(self.df.columns.values)):
            raise Exception("the data file is not in the correct format."
                            "Columns should include ['participant_id', 'session_id', 'diagnosis']")
        participant_list = list(self.df['participant_id'])
        session_list = list(self.df['session_id'])
        label_list = list(self.df['diagnosis'])

        self.patch_participant_list = participant_list
        self.patch_session_list = session_list
        self.patch_label_list = label_list

    def __len__(self):
        return len(self.patch_participant_list)

    def __getitem__(self, idx):
        img_name = self.patch_participant_list[idx]
        sess_name = self.patch_session_list[idx]
        img_label = self.patch_label_list[idx]
        label = self.diagnosis_code[img_label]

        patch_path = os.path.join(self.caps_directory, 'subjects', img_name, sess_name, 't1',
                                  'preprocessing_dl',
                                  img_name + '_' + sess_name + '_space-MNI_res-1x1x1_patchsize-' + str(self.patch_size) + '_stride-' + str(self.stride_size) + '_patch-' + str(
                                      self.index_patch) + '.pt')

        patch = torch.load(patch_path)

        # check if the patch has NAN value
        if torch.isnan(patch).any() == True:
            print("Double check, this patch has Nan value: %s" % str(img_name + '_' + sess_name + str(self.index_patch)))
            patch[torch.isnan(patch)] = 0

        if self.transformations:
            patch = self.transformations(patch)

        sample = {'image_id': img_name + '_' + sess_name + '_patch' + str(self.index_patch), 'image': patch, 'label': label}

        return sample

#
# def load_split_by_task(diagnoses_tsv, val_size=0.15, random_state=None):
#     """
#     Returns the paths of the TSV files for each set based on the task. The training and validation data has been age,sex correceted split
#
#     :param diagnoses_tsv: (str) path to the tsv file with diagnoses
#     :param val_size: (float) the proportion of the training set used for validation
#     :return: 3 Strings
#         training_tsv
#         valid_tsv
#     """
#     sets_dir = path.join(path.dirname(diagnoses_tsv),
#                          path.basename(diagnoses_tsv).split('.')[0],
#                          'val_size-' + str(val_size))
#
#     training_tsv = path.join(sets_dir, 'train.tsv')
#     valid_tsv = path.join(sets_dir, 'valid.tsv')
#
#     if not path.exists(training_tsv) or not path.exists(valid_tsv):
#         split_subjects_to_tsv(diagnoses_tsv, val_size, random_state=random_state)
#
#         training_tsv = path.join(sets_dir, 'train.tsv')
#         valid_tsv = path.join(sets_dir, 'valid.tsv')
#
#     return training_tsv, valid_tsv
#
# def load_split_by_diagnosis(options, split, n_splits=5, baseline_or_longitudinal='baseline', autoencoder=True):
#     """
#     Creates a DataFrame for training and validation sets given the wanted diagnoses, this is helpful to train the autoencoder with maximum availble data
#
#     :param options: object of the argparser
#     :param diagnoses_list: list of diagnoses to select to construct the DataFrames
#     :param baseline: bool choose to use baseline only instead of all data available
#     :return:
#         train_df DataFrame with training data
#         valid_df DataFrame with validation data
#     """
#     train_df = pd.DataFrame()
#     valid_df = pd.DataFrame()
#
#     if n_splits is None:
#         train_path = path.join(options.diagnosis_tsv_path, 'train')
#         valid_path = path.join(options.diagnosis_tsv_path, 'validation')
#
#     else:
#         train_path = path.join(options.diagnosis_tsv_path, 'train_splits-' + str(n_splits),
#                                'split-' + str(split))
#         valid_path = path.join(options.diagnosis_tsv_path, 'validation_splits-' + str(n_splits),
#                                'split-' + str(split))
#     print("Train", train_path)
#     print("Valid", valid_path)
#
#     for diagnosis in options.diagnoses_list:
#
#         if baseline_or_longitudinal == 'baseline':
#             train_diagnosis_tsv = path.join(train_path, diagnosis + '_baseline.tsv')
#         else:
#             train_diagnosis_tsv = path.join(train_path, diagnosis + '.tsv')
#
#         valid_diagnosis_tsv = path.join(valid_path, diagnosis + '_baseline.tsv')
#
#         train_diagnosis_df = pd.read_csv(train_diagnosis_tsv, sep='\t')
#         valid_diagnosis_df = pd.read_csv(valid_diagnosis_tsv, sep='\t')
#
#         train_df = pd.concat([train_df, train_diagnosis_df])
#         valid_df = pd.concat([valid_df, valid_diagnosis_df])
#
#     train_df.reset_index(inplace=True, drop=True)
#     valid_df.reset_index(inplace=True, drop=True)
#
#     if autoencoder == True:
#         train_tsv = os.path.join(tempfile.mkdtemp(), 'AE_training_subjects.tsv')
#         train_df.to_csv(train_tsv, index=False, sep='\t', encoding='utf-8')
#         valid_tsv = os.path.join(tempfile.mkdtemp(), 'AE_validation_subjects.tsv')
#         valid_df.to_csv(valid_tsv, index=False, sep='\t', encoding='utf-8')
#     else:
#         train_tsv = os.path.join(tempfile.mkdtemp(), 'CNN_training_subjects.tsv')
#         train_df.to_csv(train_tsv, index=False, sep='\t', encoding='utf-8')
#         valid_tsv = os.path.join(tempfile.mkdtemp(), 'CNN_validation_subjects.tsv')
#         valid_df.to_csv(valid_tsv, index=False, sep='\t', encoding='utf-8')
#
#     return train_df, valid_df, train_tsv, valid_tsv

def extract_patch_from_mri(image_tensor, index_patch, patch_size, stride_size, patchs_per_patient):

    ## use classifiers tensor.upfold to crop the patch.
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

# def save_checkpoint(state, is_best, checkpoint_dir, filename='checkpoint.pth.tar'):
#     """
#     This is the function to save the best model during validation process
#     :param state: the parameters that you wanna save
#     :param is_best: if the performance is better than before
#     :param checkpoint_dir:
#     :param filename:
#     :return:
#         checkpoint.pth.tar: this is the model trained by the last epoch, useful to retrain from this stopping point
#         model_best.pth.tar: if is_best is Ture, this is the best model during the validation, useful for testing the performances of the model
#     """
#     import shutil, os
#     if not os.path.exists(checkpoint_dir):
#         os.makedirs(checkpoint_dir)
#     torch.save(state, os.path.join(checkpoint_dir, filename))
#     if is_best:
#         shutil.copyfile(os.path.join(checkpoint_dir, filename),  os.path.join(checkpoint_dir, 'model_best.pth.tar'))


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

    if not os.path.exists(results_path):
        os.makedirs(results_path)
    # set the model to be eval
    ae.eval()
    output = ae(data)
    reconstructed_nii = nib.Nifti1Image(output[0][0].cpu().detach().numpy(), np.eye(4))
    input_nii = nib.Nifti1Image(data[0][0].cpu().detach().numpy(), np.eye(4))
    nib.save(reconstructed_nii, os.path.join(results_path, 'example_patch_reconstructed.nii.gz'))
    nib.save(input_nii, os.path.join(results_path, 'example_patch_original.nii.gz'))

def commandline_to_jason(commanline, pretrain_ae=False):
    """
    This is a function to write the python argparse object into a jason file. This helps for DL when searching for hyperparameters
    :param commanline: a tuple contain the output of `parser.parse_known_args()`
    :return:
    """
    import json, os

    commandline_arg_dic = vars(commanline[0])
    ## add unknown args too
    commandline_arg_dic['unknown_arg'] = commanline[1]

    ## if train_from_stop_point, do not delete this folders
    if "train_from_stop_point" in commandline_arg_dic.keys():
        if commandline_arg_dic['train_from_stop_point']:
            print('You should be responsible to make sure you did not change any parameters to train from the stopping point with the same model!')
        else:
            if not os.path.exists(os.path.join(commandline_arg_dic['output_dir'], 'log_dir')):
                os.makedirs(os.path.join(commandline_arg_dic['output_dir'], 'log_dir'))
    else:
        ### for AE
        if not os.path.exists(commandline_arg_dic['output_dir']):
                os.makedirs(commandline_arg_dic['output_dir'])

        if commandline_arg_dic['split'] != None:
            pass
        else:
            check_and_clean(commandline_arg_dic['output_dir'])
    
    ## anyway, make sure the log_dir exist
    if not os.path.exists(os.path.join(commandline_arg_dic['output_dir'], 'log_dir')):
        os.makedirs(os.path.join(commandline_arg_dic['output_dir'], 'log_dir'))

    output_dir = commandline_arg_dic['output_dir']
    # save to json file
    json = json.dumps(commandline_arg_dic)
    if pretrain_ae:
        f = open(os.path.join(output_dir, "log_dir", "commandline_autoencoder.json"), "w")
    else:
        f = open(os.path.join(output_dir, "log_dir", "commandline_cnn.json"), "w")
    f.write(json)
    f.close()

def soft_voting_subject_level(y_ground, y_hat, subjects, proba, iteration):
    ## soft voting to get the subject-level balanced accuracy
    performance_df_subject = pd.DataFrame({'iteration': iteration,
                                           'y': y_ground,
                                           'y_hat': y_hat,
                                           'subject': subjects,
                                           'probability': proba})

    subject_df = performance_df_subject['subject']
    subject_series = subject_df.apply(extract_subject_name)
    patch_series = subject_df.apply(extract_patch_index)
    subject_df_new = pd.DataFrame({'subject': subject_series.values})
    patch_df_new = pd.DataFrame({'patch': patch_series.values})

    # replace the column in the dataframe
    performance_df_subject['subject'] = subject_df_new['subject'].values
    performance_df_subject['patch'] = patch_df_new['patch'].values

    ## selected the right classified subjects:
    right_classified_df = performance_df_subject[performance_df_subject['y_hat'] == performance_df_subject['y']]
    # right_classified_df = pd.DataFrame({'patch': right_classified_series['patch'].values})

    ## count the number of right classified patch for each patch index
    count_patchs_series = right_classified_df['patch'].value_counts(normalize=True)
    index_series = performance_df_subject['patch']
    weight_list = []
    for i in index_series:
        if i in count_patchs_series.index:
            weight = count_patchs_series[i]
        else:
            weight = 0
        weight_list.append(weight)

    weight_series = pd.Series(weight_list)
    ## add to the df
    performance_df_subject['weight'] = weight_series.values

    ## do soft majority vote
    ## y^ = arg max(sum(wj * pij))
    df_final = pd.DataFrame(columns=['subject', 'y', 'y_hat', 'iteration'])
    for subject, subject_df in performance_df_subject.groupby(['subject']):
        num_patch = len(subject_df.y_hat)
        p0_all = 0
        p1_all = 0
        for i in range(num_patch):
            ## reindex the subject_df.probability
            proba_series_reindex = subject_df.probability.reset_index()
            weight_series_reindex = subject_df.weight.reset_index()
            y_series_reindex = subject_df.y.reset_index()
            iteration_series_reindex = subject_df.iteration.reset_index()

            p0 = weight_series_reindex.weight[i] * proba_series_reindex.probability[i][0]
            p1 = weight_series_reindex.weight[i] * proba_series_reindex.probability[i][1]

            p0_all += p0
            p1_all += p1

            if i == 0:
                y = y_series_reindex.y[i]
                iteration = iteration_series_reindex.iteration[i]
        proba_list = [p0_all, p1_all]
        y_hat = proba_list.index(max(proba_list))

        row_array = np.array(list([subject, y, y_hat, iteration])).reshape(1, 4)
        row_df = pd.DataFrame(row_array, columns=['subject', 'y', 'y_hat', 'iteration'])
        df_final = df_final.append(row_df)

    results = evaluate_prediction([int(e) for e in list(df_final.y)], [int(e) for e in list(
        df_final.y_hat)])  ## Note, y_hat here is not int, is string
    del results['confusion_matrix']

    return results

def load_model_test(model, checkpoint_dir, filename):
    """
    This is to load a saved model for testing
    :param model:
    :param checkpoint_dir:
    :param filename:
    :return:
    """
    from copy import deepcopy

    ## set the model to be eval mode, we explicitly think that the model was saved in eval mode, otherwise, it will affects the BN and dropout
    model.eval()
    model_updated = deepcopy(model)
    param_dict = torch.load(os.path.join(checkpoint_dir, filename))
    model_updated.load_state_dict(param_dict['model'])

    return model_updated, param_dict['global_step'], param_dict['epoch'], param_dict['best_predict']

def multi_cnn_soft_majority_voting(output_dir, fi, num_cnn, weight_list, mode='test'):
    """
    This is a function to do soft majority voting based on the num_cnn CNNs' performances
    :param output_dir:
    :param fi:
    :param num_cnn:
    :return:
    """
    y_hat = []
    ## read the validation patch-level results.
    for i in range(num_cnn):
        # load the best trained model during the training
        if i == 0:
            df = pd.io.parsers.read_csv(os.path.join(output_dir, 'performances', "fold_" + str(fi), 'cnn-' + str(i),
                                                     mode + '_patch_level_result-patch_index.tsv'), sep='\t')
            df_final = pd.DataFrame(columns=['subject', 'y', 'y_hat'])
            df_final['subject'] = df['subject'].apply(extract_subject_name)
            df_final['y'] = df['y']

        ##TODO, this is not correct, this is just the probability of the last epoch of validation
        tsv_path = os.path.join(output_dir, 'performances', "fold_" + str(fi), 'cnn-' + str(i), mode + '_patch_level_result-patch_index.tsv')
        proba_series = pd.io.parsers.read_csv(tsv_path, sep='\t')['probability']
        p0s = []
        p1s = []
        for j in range(len(proba_series)):
            p0 = weight_list[i] * eval(proba_series[j])[0]
            p1 = weight_list[i] * eval(proba_series[j])[1]
            p0s.append(p0)
            p1s.append(p1)
        p0s_series = pd.Series(p0s)
        p1s_series = pd.Series(p1s)

        ## adding the series into the final DataFrame
        ## insert the column of iteration
        df_final['cnn_' + str(i) + '_p0'] = p0s_series
        df_final['cnn_' + str(i) + '_p1'] = p1s_series

    ## based on the p0 and p1 from all the CNNs, calculate the y_hat
    p0_final = []
    p1_final = []
    for k in range(num_cnn):
        p0_final.append(df_final['cnn_' + str(k) + '_p0'].tolist())
    for k in range(num_cnn):
        p1_final.append(df_final['cnn_' + str(k) + '_p1'].tolist())

    ## element-wise adding to calcuate the final probability
    p0_soft = [sum(x) for x in zip(*p0_final)]
    p1_soft = [sum(x) for x in zip(*p1_final)]

    for m in range(len(p0_soft)):
        proba_list = [p0_soft[m], p1_soft[m]]
        y_pred = proba_list.index(max(proba_list))
        y_hat.append(y_pred)

    ### convert y_hat list ot series then add into the dataframe
    y_hat_series = pd.Series(y_hat)
    p0_soft_series = pd.Series(p0_soft)
    p1_soft_series = pd.Series(p1_soft)
    df_final['y_hat'] = y_hat_series
    df_final['p0_soft'] = p0_soft_series
    df_final['p1_soft'] = p1_soft_series

    ## save the results into output_dir
    results_soft_tsv_path = os.path.join(output_dir, 'performances', "fold_" + str(fi),
                 'validation_subject_level_result_soft_vote_multi_cnn.tsv')
    df_final.to_csv(results_soft_tsv_path, index=False, sep='\t', encoding='utf-8')


    results = evaluate_prediction([int(e) for e in list(df_final.y)], [int(e) for e in list(
        df_final.y_hat)])  ## Note, y_hat here is not int, is string
    del results['confusion_matrix']

    metrics_soft_tsv_path = os.path.join(output_dir, 'performances', "fold_" + str(fi), mode + '_subject_level_metrics_soft_vote_multi_cnn.tsv')
    pd.DataFrame(results, index=[0]).to_csv(metrics_soft_tsv_path, index=False, sep='\t', encoding='utf-8')


def weight_by_validation_acc(model, options):

    ## get the weight for soft voting system from all validation acc from all N classifiers
    best_acc_cnns = []
    for n in range(options.num_cnn):
        # load the best trained model during the training
        _, _, _, best_predict = load_model_test(model, os.path.join(options.output_dir, 'best_model_dir',
                                                                                       "fold_" + str(options.n_fold), 'cnn-' + str(n), options.best_model_criteria),
                                                                          filename='model_best.pth.tar')
        best_acc_cnns.append(best_predict)

    ## delete the weak classifiers whose acc is smaller than 0.7
    weight_list = [0 if x < 0.7 else x for x in best_acc_cnns]
    weight_list = [x / sum(weight_list) for x in weight_list]
    if all(i == 0 for i in weight_list):
        raise Exception("The ensemble learning does not really work, all classifiers work bad!")

    return weight_list