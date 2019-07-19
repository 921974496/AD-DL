
if __name__ == "__main__":
    import argparse
    import pandas as pd
    from tsv_utils import first_session, next_session, add_demographics
    import os
    from os import path
    from copy import copy
    import numpy as np

    parser = argparse.ArgumentParser(description="Argparser for data formatting")

    # Mandatory arguments
    parser.add_argument("merged_tsv", type=str,
                        help="Path to the file obtained by the command clinica iotools merge-tsv.")
    parser.add_argument("formatted_data_path", type=str,
                        help="Path to the folder containing formatted data.")
    parser.add_argument("results_path", type=str,
                        help="Path to the resulting tsv file (filename included).")

    # Modality selection
    parser.add_argument("--diagnoses", nargs="+", type=str, choices=['AD', 'CN', 'MCI', 'sMCI', 'pMCI'],
                        default=['AD', 'CN'], help="Diagnosis that must be selected from the tsv file")
    parser.add_argument("--mmse_name", type=str, default="MMS",
                        help="Name of the variable related to the MMSE score in the merged tsv file.")

    args = parser.parse_args()

    merged_df = pd.read_csv(args.merged_tsv, sep='\t')
    merged_df.set_index(['participant_id', 'session_id'], inplace=True)
    parent_directory = path.abspath(path.join(args.results_path, os.pardir))
    if not path.exists(parent_directory):
        os.makedirs(parent_directory)

    fields_dict = {'age': 'age', 'sex': 'sex', 'MMSE': args.mmse_name, 'CDR': 'cdr_global'}

    columns = ['n_subjects', 'mean_age', 'std_age', 'min_age', 'max_age', 'sexF', 'sexM', 'mean_MMSE', 'std_MMSE',
               'min_MMSE', 'max_MMSE', 'CDR_0', 'CDR_0.5', 'CDR_1', 'CDR_2', 'CDR_3', 'mean_scans', 'std_scans',
               'n_scans']
    results_df = pd.DataFrame(index=args.diagnoses, columns=columns, data=np.zeros((len(args.diagnoses), len(columns))))

    # Need all values for mean and variance (age and MMSE)
    diagnosis_dict = dict.fromkeys(args.diagnoses)
    for diagnosis in args.diagnoses:
        diagnosis_dict[diagnosis] = {'age': [], 'MMSE': [], 'scans': []}
        diagnosis_df = pd.read_csv(path.join(args.formatted_data_path, 'lists_by_diagnosis', diagnosis + '.tsv'),
                                   sep='\t')
        diagnosis_demographics_df = add_demographics(diagnosis_df, merged_df, diagnosis)
        diagnosis_demographics_df.set_index(['participant_id', 'session_id'], inplace=True)
        diagnosis_df.set_index(['participant_id', 'session_id'], inplace=True)

        for subject, subject_df in diagnosis_df.groupby(level=0):
            first_session_id = first_session(subject_df)
            feature_absence = isinstance(merged_df.loc[(subject, first_session_id), 'diagnosis'], float)
            while feature_absence:
                print(subject, first_session_id)
                first_session_id = next_session(subject_df, first_session_id)
                feature_absence = isinstance(merged_df.loc[(subject, first_session_id), 'diagnosis'], float)
            demographics_subject_df = merged_df.loc[subject]

            # Extract features
            results_df.loc[diagnosis, 'n_subjects'] += 1
            results_df.loc[diagnosis, 'n_scans'] += len(subject_df)
            diagnosis_dict[diagnosis]['age'].append(
                merged_df.loc[(subject, first_session_id), fields_dict['age']])
            diagnosis_dict[diagnosis]['MMSE'].append(
                merged_df.loc[(subject, first_session_id), fields_dict['MMSE']])
            diagnosis_dict[diagnosis]['scans'].append(len(subject_df))
            sexF = len(demographics_subject_df[(demographics_subject_df[fields_dict['sex']].isin(['F']))]) > 0
            sexM = len(demographics_subject_df[(demographics_subject_df[fields_dict['sex']].isin(['M']))]) > 0
            if sexF:
                results_df.loc[diagnosis, 'sexF'] += 1
            elif sexM:
                results_df.loc[diagnosis, 'sexM'] += 1
            else:
                raise ValueError('Patient %s has no sex' % subject)

            cdr = merged_df.loc[(subject, first_session_id), fields_dict['CDR']]
            if cdr == 0:
                results_df.loc[diagnosis, 'CDR_0'] += 1
            elif cdr == 0.5:
                results_df.loc[diagnosis, 'CDR_0.5'] += 1
            elif cdr == 1:
                results_df.loc[diagnosis, 'CDR_1'] += 1
            elif cdr == 2:
                results_df.loc[diagnosis, 'CDR_2'] += 1
            elif cdr == 3:
                results_df.loc[diagnosis, 'CDR_3'] += 1
            else:
                raise ValueError('Patient %s has CDR %f' % (subject, cdr))

    for diagnosis in args.diagnoses:
        results_df.loc[diagnosis, 'mean_age'] = np.nanmean(diagnosis_dict[diagnosis]['age'])
        results_df.loc[diagnosis, 'std_age'] = np.nanstd(diagnosis_dict[diagnosis]['age'])
        results_df.loc[diagnosis, 'min_age'] = np.min(diagnosis_dict[diagnosis]['age'])
        results_df.loc[diagnosis, 'max_age'] = np.max(diagnosis_dict[diagnosis]['age'])
        results_df.loc[diagnosis, 'mean_MMSE'] = np.nanmean(diagnosis_dict[diagnosis]['MMSE'])
        results_df.loc[diagnosis, 'std_MMSE'] = np.nanstd(diagnosis_dict[diagnosis]['MMSE'])
        results_df.loc[diagnosis, 'min_MMSE'] = np.nanmin(diagnosis_dict[diagnosis]['MMSE'])
        results_df.loc[diagnosis, 'max_MMSE'] = np.nanmax(diagnosis_dict[diagnosis]['MMSE'])
        results_df.loc[diagnosis, 'mean_scans'] = np.nanmean(diagnosis_dict[diagnosis]['scans'])
        results_df.loc[diagnosis, 'std_scans'] = np.nanstd(diagnosis_dict[diagnosis]['scans'])

    print(results_df)

    results_df.to_csv(args.results_path, sep='\t')
