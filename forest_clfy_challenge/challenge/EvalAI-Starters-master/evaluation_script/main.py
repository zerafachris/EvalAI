import random
import pandas as pd
from sklearn import metrics as MC


def evaluate(test_annotation_file, user_submission_file, phase_codename, **kwargs):
    print("Starting Evaluation.....")
    print("Submission related metadata:")
    """
    Evaluates the submission for a particular challenge phase adn returns score
    Arguments:

        `test_annotations_file`: Path to test_annotation_file on the server
        `user_submission_file`: Path to file submitted by the user
        `phase_codename`: Phase to which submission is made

        `**kwargs`: keyword arguments that contains additional submission
        metadata that challenge hosts can use to send slack notification.
        You can access the submission metadata
        with kwargs['submission_metadata']

        Example: A sample submission metadata can be accessed like this:
        >>> print(kwargs['submission_metadata'])
        {
            'status': u'running',
            'when_made_public': None,
            'participant_team': 5,
            'input_file': 'https://abc.xyz/path/to/submission/file.json',
            'execution_time': u'123',
            'publication_url': u'ABC',
            'challenge_phase': 1,
            'created_by': u'ABC',
            'stdout_file': 'https://abc.xyz/path/to/stdout/file.json',
            'method_name': u'Test',
            'stderr_file': 'https://abc.xyz/path/to/stderr/file.json',
            'participant_team_name': u'Test Team',
            'project_url': u'http://foo.bar',
            'method_description': u'ABC',
            'is_public': False,
            'submission_result_file': 'https://abc.xyz/path/result/file.json',
            'id': 123,
            'submitted_at': u'2017-03-20T19:22:03.880652Z'
        }
    """
    print(kwargs["submission_metadata"])

    print("Loading true data")
    df_true = pd.read_csv(test_annotation_file)
    df_true.columns = ['ID', 'y_true']

    print("Loading submitted data")
    df_submit = pd.read_csv(user_submission_file)
    df_submit.columns = ['ID', 'y_pred']

    print("Merge")
    df = pd.merge(df_true, df_submit, how='left', left_on='ID', right_on='ID')

    num_nulls = sum(df['y_pred'].isnull())
    print('\nNumber of nulls : {}'.format(num_nulls))
    print('\nAre Labels identical: {}'.format(set(df_true['y_true']) == set(df_submit['y_pred']) ))

    print("Evaluating of Metrics")
    m_accuracy = MC.accuracy_score(df.y_true, df.y_pred)
    m_precision = MC.precision_score(df.y_true, df.y_pred,  average='weighted')
    m_recall = MC.recall_score(df.y_true, df.y_pred, average='weighted')
    m_f1_score = MC.f1_score(df.y_true, df.y_pred, average='weighted')

    print("\nMetric1 : Accuracy {}".format(m_accuracy))
    print("\nMetric2 : Precision {}".format(m_precision))
    print("\nMetric3 : Recall {}".format(m_recall))
    print("\nTotal : f1_score {}".format(m_f1_score))


    train_split = {"Accuracy": m_accuracy,
                    "Precision": m_precision,
                    "Recall": m_recall,
                    "F1-Score":   m_f1_score,
                    }

    output = {}
    if phase_codename == "challenge":
        output["result"] = [
                            {"challenge": train_split}
                            ]
        output["submission_result"] = output["result"][0]["challenge"]
        print("Completed evaluation")

    return output
