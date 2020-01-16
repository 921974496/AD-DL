"""
This file generates data for trivial or intractable (random) data for binary classification.
"""
import pandas as pd
import numpy as np
import argparse
import nibabel as nib
from os import path
import os


def generate_random_dataset(caps_dir, data_df, output_dir, n_subjects, mean=0, sigma=0.1):
    """
    Generates an intractable classification task from the first subject of the tsv file.

    :param caps_dir: (str) path to the CAPS directory.
    :param data_df: (DataFrame) list of subjects/sessions.
    :param output_dir: (str) folder containing the synthetic dataset in CAPS format
    :param n_subjects: (int) number of subjects in each class of the synthetic dataset
    :param mean: (float) mean of the gaussian noise
    :param sigma: (float) standard deviation of the gaussian noise
    """
    # Create subjects dir
    if not path.exists(path.join(output_dir, 'subjects')):
        os.makedirs(path.join(output_dir, 'subjects'))

    # Retrieve image of first subject
    participant_id = data_df.iloc[0, 'participant_id']
    session_id = data_df.iloc[0, 'session_id']
    image_path = path.join(caps_dir, 'subjects', participant_id, session_id,
                           't1', 'spm', 'segmentation', 'normalized_space',
                           participant_id + '_' + session_id +
                           '_T1w_segm-graymatter_space-Ixi549Space_modulated-off_probability.nii.gz')
    image_nii = nib.load(image_path)
    image = image_nii.get_data()

    # Create output tsv file
    participant_id_list = ['sub-RAND%i' % i for i in range(2 * n_subjects)]
    session_id_list = ['ses-M00'] * 2 * n_subjects
    diagnosis_list = ['AD'] * n_subjects + ['CN'] * n_subjects
    data = np.array([participant_id_list, session_id_list, diagnosis_list])
    data = data.T
    output_df = pd.DataFrame(data, columns=['participant_id', 'session_id', 'diagnosis'])
    output_df.to_csv(path.join(caps_dir, 'data.tsv'), sep='\t', index=False)

    for i in range(2 * n_subjects):
        gauss = np.random.normal(mean, sigma, image.shape)
        participant_id = 'sub-RAND%i' % i
        noisy_image = image + gauss
        noisy_image_nii = nib.Nifti1Image(noisy_image, header=image_nii.header, affine=image_nii.affine)
        noisy_image_nii_path = path.join(output_dir, 'subjects', participant_id, 'ses-M00', 't1', 'preprocessing_dl')
        noisy_image_nii_filename = participant_id + '_ses-M00_space-MNI_res-1x1x1.nii.gz'
        if not path.exists(noisy_image_nii_path):
            os.makedirs(noisy_image_nii_path)
        nib.save(noisy_image_nii, path.join(noisy_image_nii_path, noisy_image_nii_filename))


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Argparser for synthetic data generation")

    parser.add_argument("caps_dir", type=str,
                        help="Data using CAPS structure. Must include the output of t1-volume-tissue-segmentation "
                             "pipeline of clinica.")
    parser.add_argument("tsv_file", type=str,
                        help="tsv file with subjets/sessions to process.")
    parser.add_argument("output_dir", type=str,
                        help="Folder containing the final dataset in CAPS format.")

    parser.add_argument("--selection", default="trivial", type=str, choices=["trivial", "random"],
                        help="Chooses which type of synthetic dataset is wanted.")
    parser.add_argument("--n_subjects", type=int, default=300,
                        help="Number of subjects in each class of the synthetic dataset.")

    parsed_args = parser.parse_known_args()
    options = parsed_args[0]
    if parsed_args[1]:
        print("unknown arguments: %s" % parser.parse_known_args()[1])

    data_df = pd.read_csv(options.tsv_file, sep='\t')

    if options.selection == 'random':
        generate_random_dataset(options.caps_dir, data_df, options.output_dir, options.n_subjects)
    else:
        pass
