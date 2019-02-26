import argparse
from torch.utils.data import DataLoader
from tensorboardX import SummaryWriter
from classification_utils import *
from Code.classifiers.utils import EarlyStopping
import copy
from model import *
import torchvision.transforms as transforms

__author__ = "Junhao Wen"
__copyright__ = "Copyright 2018 The Aramis Lab Team"
__credits__ = ["Junhao Wen"]
__license__ = "See LICENSE.txt file"
__version__ = "0.1.0"
__maintainer__ = "Junhao Wen"
__email__ = "junhao.wen89@gmail.com"
__status__ = "Development"

parser = argparse.ArgumentParser(description="Argparser for Pytorch 3D patch CNN")

## Data arguments
parser.add_argument("--caps_directory", default='/network/lustre/dtlake01/aramis/projects/clinica/CLINICA_datasets/CAPS/Frontiers_DL/ADNI',
                           help="Path to the caps of image processing pipeline of DL")
parser.add_argument("--diagnosis_tsv_path", default='/teams/ARAMIS/PROJECTS/junhao.wen/PhD/ADNI_classification/gitlabs/AD-DL/tsv_files/tsv_after_data_splits/Elina_version/data/ADNI/lists_by_diagnosis/train',
                           help="Path to tsv file of the population based on the diagnosis tsv files. To note, the column name should be participant_id, session_id and diagnosis.")
parser.add_argument("--output_dir", default='/teams/ARAMIS/PROJECTS/junhao.wen/PhD/ADNI_classification/gitlabs/AD-DL/Results/pytorch_ae_conv',
                           help="Path to store the classification outputs, including log files for tensorboard usage and also the tsv files containg the performances.")
parser.add_argument("--data_type", default="from_patch", choices=["from_MRI", "from_patch"],
                    help="Use which data to train the model, as extract slices from MRI is time-consuming, we recommand to run the postprocessing pipeline and train from slice data")
parser.add_argument("--patch_size", default=50, type=int,
                    help="The patch size extracted from the MRI")
parser.add_argument("--patch_stride", default=50, type=int,
                    help="The stride for the patch extract window from the MRI")
parser.add_argument("--batch_size", default=1, type=int,
                    help="Batch size for training. (default=1)")
parser.add_argument("--num_workers", default=0, type=int,
                    help='the number of batch being loaded in parallel')
parser.add_argument('--baseline_or_longitudinal', default="baseline", choices=["baseline", "longitudinal"],
                    help="Using baseline scans or all available longitudinal scans for training")
parser.add_argument('--hippocampus_roi', default=True, type=bool,
                    help="If train the model using only hippocampus ROI")

# transfer learning
parser.add_argument("--network", default="Conv_4_FC_2", choices=["Conv_4_FC_2", "Conv_7_FC_2", "Conv_3_FC_2"],
                    help="Autoencoder network type. (default=Conv_4_FC_2). Also, you can try training from scratch using VoxResNet and AllConvNet3D")
parser.add_argument("--transfer_learning_autoencoder", default=True, type=bool,
                    help="If do transfer learning using autoencoder, the learnt weights will be transferred. Should be exclusive with net_work")
parser.add_argument("--diagnoses_list", default=["sMCI", "pMCI"], type=str,
                    help="Labels based on binary classification")

# Training arguments
parser.add_argument("--epochs", default=10, type=int,
                    help="Epochs through the data. (default=20)")
parser.add_argument("-lr", "--learning_rate", default=1e-3, type=float,
                    help="Learning rate of the optimization. (default=0.01)")
parser.add_argument("--n_splits", default=5, type=int,
                    help="Define the cross validation, by default, we use 5-fold.")
parser.add_argument("--split", default=None, type=int,
                    help="Define a specific fold in the k-fold, this is very useful to find the optimal model, where you do not want to run your k-fold validation")
parser.add_argument("--optimizer", default="Adam", choices=["SGD", "Adadelta", "Adam"],
                    help="Optimizer of choice for training. (default=Adam)")
parser.add_argument('--use_gpu', default=True, type=bool,
                    help='Uses gpu instead of cpu if cuda is available')
parser.add_argument('--weight_decay', default=1e-4, type=float,
                    help='weight decay (default: 1e-4)')
## TODO
parser.add_argument("--train_from_stop_point", default=False, type=bool,
                    help='If train a network from the very beginning or from the point where it stopped, where the network is saved by tensorboardX')

## early stopping arguments
parser.add_argument("--patience", type=int, default=10,
                    help="tolerated epochs without improving for early stopping.")
parser.add_argument("--tolerance", type=float, default=0,
                    help="Tolerance of magnitude of performance after each epoch.")

def main(options):

    ## Train the model with pretrained AE
    if options.transfer_learning_autoencoder:
        print('Train the model with the weights from a pre-trained model by autoencoder!')
        print('The chosen network is %s !' % options.network)
        print('The chosen network should correspond to the encoder of the stacked pretrained AE')

        try:
            model = eval(options.network)()
        except:
            raise Exception('The model has not been implemented or has bugs in the model codes')
    else:
        print('Train the model from scratch!')
        print('The chosen network is %s !' % options.network)
        try:
            model = eval(options.network)()
        except:
            raise Exception('The model has not been implemented')

    if options.split != None:
        print("Only run for a specific fold, meaning that you are trying to find your optimal model by exploring your training and validation data")
        options.n_splits = 1

    for fi in range(options.n_splits):

        # to set the split = 0
        if options.split != None:
            ## train seperately a specific fold during the k-fold, also good for the limitation of your comuptational power
            _, _, training_tsv, valid_tsv = load_split_by_diagnosis(options, options.split, baseline_or_longitudinal=options.baseline_or_longitudinal, autoencoder=False)
            fi = options.split
        else:
             _, _, training_tsv, valid_tsv = load_split_by_diagnosis(options, fi, baseline_or_longitudinal=options.baseline_or_longitudinal, autoencoder=False)

        print("Running for the %d -th fold" % fi)
        print("Train the model from 0 epoch")

        if options.transfer_learning_autoencoder:
            model, saved_epoch = load_model_after_ae(model, os.path.join(options.output_dir, 'best_model_dir', "fold_" + str(fi),
                                                               'ConvAutoencoder', 'fine_tune', 'Encoder'),
                                           filename='model_best_encoder.pth.tar')

            print("The AE was saved at %s -th epoch" % str(saved_epoch))

        ## the inital model weight and bias after AE
        init_state = copy.deepcopy(model.state_dict())

        ## need to normalized the value to [0, 1]
        transformations = transforms.Compose([NormalizeMinMax()])
        if options.hippocampus_roi:
            print("Only using hippocampus ROI")

            data_train = MRIDataset_patch_hippocampus(options.caps_directory, training_tsv, transformations=transformations)
            data_valid = MRIDataset_patch_hippocampus(options.caps_directory, valid_tsv, transformations=transformations)

        else:
            data_train = MRIDataset_patch(options.caps_directory, training_tsv, options.patch_size, options.patch_stride, transformations=transformations, data_type=options.data_type)
            data_valid = MRIDataset_patch(options.caps_directory, valid_tsv, options.patch_size, options.patch_stride, transformations=transformations, data_type=options.data_type)

        # Use argument load to distinguish training and testing
        train_loader = DataLoader(data_train,
                                  batch_size=options.batch_size,
                                  shuffle=True,
                                  num_workers=options.num_workers,
                                  drop_last=True,
                                  )

        valid_loader = DataLoader(data_valid,
                                  batch_size=options.batch_size,
                                  shuffle=False,
                                  num_workers=options.num_workers,
                                  drop_last=False,
                                  )

        lr = options.learning_rate
        # chosen optimer for back-propogation
        optimizer = eval("torch.optim." + options.optimizer)(filter(lambda x: x.requires_grad, model.parameters()), lr,
                                                             weight_decay=options.weight_decay)
        # apply exponential decay for learning rate
        scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.995)

        global_step = 0
        model.load_state_dict(init_state)

        ## Decide to use gpu or cpu to train the model
        if options.use_gpu == False:
            model.cpu()
            example_batch = next(iter(train_loader))['image']

        else:
            print("Using GPU")
            model.cuda()
            ## example image for tensorbordX usage:$
            example_batch = next(iter(train_loader))['image'].cuda()

        # Define loss and optimizer
        loss = torch.nn.CrossEntropyLoss()

        print('Beginning the training task')
        # parameters used in training
        best_accuracy = 0.0
        best_loss_valid = np.inf
        writer_train_batch = SummaryWriter(log_dir=(os.path.join(options.output_dir, "log_dir", "fold_" + str(fi), "CNN", "train_batch")))
        writer_train_all_data = SummaryWriter(log_dir=(os.path.join(options.output_dir, "log_dir", "fold_" + str(fi), "CNN", "train_all_data")))
        
        writer_valid = SummaryWriter(log_dir=(os.path.join(options.output_dir, "log_dir", "fold_" + str(fi), "CNN", "valid")))

        ## get the info for training and write them into tsv files.
        ## only save the last epoch, if you wanna check the performances during training, using tensorboard
        train_subjects = []
        valid_subjects = []
        y_grounds_train = []
        y_grounds_valid = []
        y_hats_train = []
        y_hats_valid = []
        train_probas = []
        valid_probas = []

        # initialize the early stopping instance
        early_stopping = EarlyStopping('loss', min_delta=options.tolerance, patience=options.patience)

        for epoch in range(options.epochs):
            print("At %s -th epoch." % str(epoch))

            # train the model
            train_subject, y_ground_train, y_hat_train, train_proba, acc_mean_train, global_step, loss_batch_mean_train = train(model, train_loader, options.use_gpu, loss, optimizer, writer_train_batch, epoch, fi, model_mode='train', global_step=global_step)

            # calculate the training accuracy based on all the training data
            train_subject_all, y_ground_train_all, y_hat_train_all, train_proba_all, acc_mean_train_all, _, loss_batch_mean_train_all = train(model, train_loader, options.use_gpu, loss, optimizer, writer_train_all_data, epoch, fi, model_mode='valid', global_step=global_step)
            print("For training, subject level balanced accuracy is %f at the end of epoch %d" % (acc_mean_train_all, epoch))

            ## at then end of each epoch, we validate one time for the model with the validation data
            valid_subject, y_ground_valid, y_hat_valid, valide_proba, acc_mean_valid, global_step, loss_batch_mean_valid = train(model, valid_loader, options.use_gpu, loss, optimizer, writer_valid, epoch, fi, model_mode='valid', global_step=global_step)
            print("For validation, subject level balanced accuracy is %f at the end of epoch %d" % (acc_mean_valid, epoch))

            ## update the learing rate
            if epoch % 20 == 0 and epoch != 0:
                scheduler.step()

            # save the best model based on the best acc
            is_best = acc_mean_valid > best_accuracy
            best_accuracy = max(best_accuracy, acc_mean_valid)
            save_checkpoint({
                'epoch': epoch + 1,
                'model': model.state_dict(),
                'best_predict': best_accuracy,
                'optimizer': optimizer.state_dict(),
                'global_step': global_step
            }, is_best, os.path.join(options.output_dir, "best_model_dir", "fold_" + str(fi), "CNN", 'best_acc'))

            # save the best model based on the best loss
            is_best = loss_batch_mean_valid < best_loss_valid
            best_loss_valid = min(loss_batch_mean_valid, best_loss_valid)
            save_checkpoint({
                'epoch': epoch + 1,
                'model': model.state_dict(),
                'best_loss': best_loss_valid,
                'optimizer': optimizer.state_dict(),
                'global_step': global_step
            }, is_best, os.path.join(options.output_dir, "best_model_dir", "fold_" + str(fi), "CNN", "best_loss"))

            ## try early stopping criterion
            if early_stopping.step(loss_batch_mean_valid) or epoch == options.epochs - 1:
                print("By applying early stopping or at the last epoch defnied by user, the model should be stopped training at %d-th epoch" % epoch)
                # if early stopping or last epoch, save the results into the tsv file

                train_subjects.extend(train_subject)
                y_grounds_train.extend(y_ground_train)
                y_hats_train.extend(y_hat_train)
                train_probas.extend(train_proba)
                valid_subjects.extend(valid_subject)
                y_grounds_valid.extend(y_ground_valid)
                y_hats_valid.extend(y_hat_valid)
                valid_probas.extend(valide_proba)

                break

        ## save the graph and image
        # writer_train_batch.add_graph(model, example_batch)

        ### write the information of subjects and performances into tsv files.
        ## For train & valid, we offer only hard voting for
        hard_voting_to_tsvs(options.output_dir, fi, train_subjects, y_grounds_train, y_hats_train, train_probas, mode='train')
        hard_voting_to_tsvs(options.output_dir, fi, valid_subjects, y_grounds_valid, y_hats_valid, valid_probas, mode='validation')

        torch.cuda.empty_cache()

if __name__ == "__main__":
    commandline = parser.parse_known_args()
    print("The commandline arguments:")
    print(commandline)
    ## save the commind line arguments into a tsv file for tracing all different kinds of experiments
    commandline_to_jason(commandline)
    options = commandline[0]
    if commandline[1]:
        raise Exception("unknown arguments: %s" % parser.parse_known_args()[1])
    main(options)
