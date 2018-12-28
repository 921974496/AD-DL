import argparse
import torchvision.transforms as transforms
from tensorboardX import SummaryWriter
from torch.utils.data import DataLoader
from classification_utils import *
from model import alexnet2D, lenet2D, resnet2D
import copy

__author__ = "Junhao Wen"
__copyright__ = "Copyright 2018 The Aramis Lab Team"
__credits__ = ["Junhao Wen"]
__license__ = "See LICENSE.txt file"
__version__ = "0.1.0"
__maintainer__ = "Junhao Wen"
__email__ = "junhao.wen89@gmail.com"
__status__ = "Development"

parser = argparse.ArgumentParser(description="Argparser for Pytorch 2D CNN, The input MRI's dimension is 169*208*179 after cropping")

parser.add_argument("-id", "--caps_directory", default='/teams/ARAMIS/PROJECTS/CLINICA/CLINICA_datasets/temp/CAPS_ADNI_DL',
                           help="Path to the caps of image processing pipeline of DL")
parser.add_argument("-dt", "--diagnosis_tsv", default='/teams/ARAMIS/PROJECTS/junhao.wen/PhD/ADNI_classification/gitlabs/AD-DL/tsv_files/test.tsv',
                           help="Path to tsv file of the population. To note, the column name should be participant_id, session_id and diagnosis.")
parser.add_argument("-od", "--output_dir", default='/teams/ARAMIS/PROJECTS/junhao.wen/PhD/ADNI_classification/gitlabs/AD-DL/Results/pytorch',
                           help="Path to store the classification outputs, including log files for tensorboard usage and also the tsv files containg the performances.")
parser.add_argument("-tl", "--transfer_learning", default=False, type=bool,
                           help="If do transfer learning")
parser.add_argument("-nw", "--network", default="Lenet2D", choices=["AlexNet2D", "ResNet2D", "Lenet2D", "AllConvNet2D"],
                    help="Deep network type. (default=AlexNet)")
parser.add_argument("-lr", "--learning_rate", default=1e-3, type=float,
                    help="Learning rate of the optimization. (default=0.01)")

parser.add_argument("--runs", default=1, type=int,
                    help="How many times to run the training and validation procedures with the same data split strategy, default is 1.")
parser.add_argument("--shuffle", default=True, type=bool,
                    help="Load data if shuffled or not, shuffle for training, no for test data.")
parser.add_argument("--epochs", default=1, type=int,
                    help="Epochs through the data. (default=20)")
parser.add_argument("--batch_size", default=8, type=int,
                    help="Batch size for training. (default=1)")
parser.add_argument("--optimizer", default="Adam", choices=["SGD", "Adadelta", "Adam"],
                    help="Optimizer of choice for training. (default=Adam)")
parser.add_argument("--use_gpu", default=True, nargs='+', type=bool,
                    help="If use gpu or cpu. Empty implies cpu usage.")
parser.add_argument('--force', default=True, type=bool,
                    help='If force to rerun the classification, default behavior is to clean the output folder and restart from scratch')
parser.add_argument('--mri_plane', default=0,
                    help='Which coordinate axis to take for slicing the MRI. 0 is for saggital, 1 is for coronal and 2 is for axial direction, respectively ')
parser.add_argument("--num_workers", default=4, type=int,
                    help='the number of batch being loaded in parallel')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum')
parser.add_argument('--weight_decay', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')
parser.add_argument('--random_state', default=None,
                    help='If set random state when splitting data training and validation set using StratifiedShuffleSplit')

# parser.add_argument("--estop", default=1e-2, type=float,
#                     help="Early stopping criteria on the development set. (default=1e-2)")

def main(options):

    if not os.path.exists(options.output_dir):
        os.makedirs(options.output_dir)
    if options.force == True:
        check_and_clean(options.output_dir)

    # Initial the model
    if options.transfer_learning == True:
        print('Do transfer learning with existed model trained on ImageNet!\n')
        print('The chosen network is %s !' % options.network)
        if options.network == "AlexNet2D":
            model = alexnet2D(transfer_learning=options.transfer_learning)
            trg_size = (224, 224)  ## this is the original input size of alexnet
        elif options.network == "ResNet2D":
            model = resnet2D('resnet152', pretrained=options.transfer_learning)
            trg_size = (224, 224)  ## this is the original input size of resnet
        else:
            raise Exception('The model has not been implemented')

        transformations = transforms.Compose([transforms.ToPILImage(),
                                              transforms.Resize(trg_size),
                                              transforms.ToTensor()])
    else:
        print('Train the model from scratch!')
        print('The chosen network is %s !' % options.network)
        if options.network == "Lenet2D":
            model = lenet2D(mri_plane=options.mri_plane)
        elif options.network == "AlexNet2D":
            model = alexnet2D(mri_plane=options.mri_plane, num_classes=2)
        else:
            raise Exception('The model has not been implemented')
        transformations = None

    ## the inital model weight and bias
    init_state = copy.deepcopy(model.state_dict())

    for fi in range(options.runs):
        # Get the data.
        print("Running for the %d run" % fi)
        model.load_state_dict(init_state)


        ## load the tsv file
        training_tsv, valid_tsv = load_split(options.diagnosis_tsv, val_size=0.15, random_state=options.random_state)

        data_train = MRIDataset_slice(options.caps_directory, training_tsv, transformations=transformations, transfer_learning=options.transfer_learning, mri_plane=options.mri_plane)
        data_valid = MRIDataset_slice(options.caps_directory, valid_tsv, transformations=transformations, transfer_learning=options.transfer_learning, mri_plane=options.mri_plane)

        # Use argument load to distinguish training and testing
        train_loader = DataLoader(data_train,
                                  batch_size=options.batch_size,
                                  shuffle=options.shuffle,
                                  num_workers=options.num_workers,
                                  drop_last=True,
                                  pin_memory=True)

        valid_loader = DataLoader(data_valid,
                                 batch_size=options.batch_size,
                                 shuffle=False,
                                 num_workers=options.num_workers,
                                 drop_last=True,
                                 pin_memory=True)

        ## Decide to use gpu or cpu to train the model
        if options.use_gpu == False:
            use_cuda = False
            model.cpu()
        else:
            print("Using GPU")
            use_cuda = True
            model.cuda()

        ## example image for tensorbordX usage:$
        example_batch = next(iter(train_loader))['image'].cuda()

        # Binary cross-entropy loss
        loss = torch.nn.CrossEntropyLoss()
        # initial learning rate for training
        lr = options.learning_rate
        # chosen optimer for back-propogation
        optimizer = eval("torch.optim." + options.optimizer)(filter(lambda x: x.requires_grad, model.parameters()), lr,
                                                             weight_decay=options.weight_decay)
        # apply exponential decay for learning rate
        scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.995)

        # parameters used in training
        best_accuracy = 0.0
        writer_train = SummaryWriter(log_dir=(os.path.join(options.output_dir, "log_dir", "iteration_" + str(fi), "train")))
        writer_valid = SummaryWriter(log_dir=(os.path.join(options.output_dir, "log_dir", "iteration_" + str(fi), "valid")))

        ## get the info for training and write them into tsv files.
        train_subjects = []
        valid_subjects = []
        y_grounds_train = []
        y_grounds_valid = []
        y_hats_train = []
        y_hats_valid = []

        for epoch_i in range(options.epochs):
            print("At %s -th epoch." % str(epoch_i))

            # train the model
            train_subject, y_ground_train, y_hat_train, acc_mean_train, global_steps_train = train(model, train_loader, use_cuda, loss, optimizer, writer_train, epoch_i, model_mode='train')
            train_subjects.extend(train_subject)
            y_grounds_train.extend(y_ground_train)
            y_hats_train.extend(y_hat_train)
            ## at then end of each epoch, we validate one time for the model with the validation data
            valid_subject, y_ground_valid, y_hat_valid, acc_mean_valid, global_steps_valid = train(model, valid_loader, use_cuda, loss, optimizer, writer_valid, epoch_i, model_mode='valid', global_steps=global_steps_train)
            print("Slice level average validation accuracy is %f at the end of epoch %d" % (acc_mean_valid, epoch_i))
            valid_subjects.extend(valid_subject)
            y_grounds_valid.extend(y_ground_valid)
            y_hats_valid.extend(y_hat_valid)

            ## update the learing rate
            if epoch_i % 20 == 0:
                scheduler.step()

            # save the best model on the validation dataset
            is_best = acc_mean_valid > best_accuracy
            best_accuracy = max(best_accuracy, acc_mean_valid)
            save_checkpoint({
                'epoch': epoch_i + 1,
                'state_dict': model.state_dict(),
                'best_predict': best_accuracy,
                'optimizer': optimizer.state_dict()
            }, is_best, os.path.join(options.output_dir, "best_model_dir", "iteration_" + str(fi)))

        ## save the graph and image
        writer_train.add_graph(model, example_batch)

        ### write the information of subjects and performances into tsv files.
        iteration_subjects_df_train, results_train = results_to_tsvs(options.output_dir, fi, train_subjects, y_grounds_train, y_hats_train, mode='train')
        iteration_subjects_df_valid, results_valid = results_to_tsvs(options.output_dir, fi, valid_subjects, y_grounds_valid, y_hats_valid, mode='validation')


if __name__ == "__main__":
    ret = parser.parse_known_args()
    options = ret[0]
    if ret[1]:
        print("unknown arguments: %s" % (parser.parse_known_args()[1]))
    main(options)
