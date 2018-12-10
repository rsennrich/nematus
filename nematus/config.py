import argparse
import cPickle as pkl
import collections
import json
import logging
import sys

import util


class ParameterSpecification:
    """Describes a Nematus configuration parameter.

    For many parameters, a ParameterSpecification simply gets mapped to an
    argparse.add_argument() call when reading parameters from the command-line
    (as opposed to reading from a pre-existing config file). To make this
    convenient, ParameterSpecification's constructor accepts all of
    argparse.add_argument()'s keyword arguments so they can simply be passed
    through. For parameters with more complex defintions,
    ParameterSpecification adds some supporting arguments:

      - legacy_names: a ParameterSpecification can optionally include a list of
          legacy parameter names that will be used by
          load_config_from_json_file() to automatically recognise and update
          parameters with old names when reading from a JSON file.

      - visible_arg_names / hidden_arg_names: a ParameterSpecification can
          include multiple synonyms for the command-line argument.
          read_config_from_cmdline() will automatically add these to the
          parser, making them visible (via train.py -h, etc.) or hidden from
          users.

      - derivation_func: a few parameters are derived using the values of other
          parameters after the initial pass (i.e. after argparse has parsed the
          command-line arguments or after the parameters have been loaded from
          a pre-existing JSON config). For instance, if dim_per_factor is not
          set during the initial pass then it is set to [embedding_size]
          (provided that factors == 1).

    Note that unlike arparse.add_argument(), it is required to supply a default
    value. Generally, we need a default value both for argparse.add_argument()
    and also to fill in a missing parameter value when reading a config from an
    older JSON file.

    Some parameters don't have corresponding command-line arguments (e.g.
    model_version). They can be represented as ParameterSpecification objects
    by leaving both visible_arg_names and hidden_arg_names empty.
    """

    def __init__(self, name, default, legacy_names=[], visible_arg_names=[],
                 hidden_arg_names=[], derivation_func=None, **argparse_args):
        """
        Args:
            name: string (must be a valid Python variable name).
            default: the default parameter value.
            legacy_names: list of strings.
            visible_arg_names: list of strings (all must start '-' or '--')
            hidden_arg_names: list of strings (all must start '-' or '--')
            derivation_func: function taking config and meta_config arguments.
            argparse_args: any keyword arguments accepted by argparse.
        """
        self.name = name
        self.default = default
        self.legacy_names = legacy_names
        self.visible_arg_names = visible_arg_names
        self.hidden_arg_names = hidden_arg_names
        self.derivation_func = derivation_func
        self.argparse_args = argparse_args
        if len(argparse_args) == 0:
            assert visible_arg_names == [] and hidden_arg_names == []
        else:
            self.argparse_args['default'] = default


class ConfigSpecification:
    """A collection of ParameterSpecifications representing a complete config.

    The ParameterSpecifications are organised into groups. These are used with
    argparse's add_argument_group() mechanism when constructing a command-line
    argument parser (in read_config_from_cmdline()). They don't serve any
    other role.

    The nameless '' group is used for top-level command-line arguments (or it
    would be if we had any) and for parameters that don't have corresponding
    command-line arguments.
    """

    def __init__(self):
        """Builds the collection of ParameterSpecifications."""

        # Define the parameter groups and their descriptions.
        self._group_descriptions = collections.OrderedDict()
        self._group_descriptions[''] = None
        self._group_descriptions['data'] = 'data sets; model loading and ' \
                                           'saving'
        self._group_descriptions['network'] = 'network parameters'
        self._group_descriptions['training'] = 'training parameters'
        self._group_descriptions['validation'] = 'validation parameters'
        self._group_descriptions['display'] = 'display parameters'
        self._group_descriptions['translate'] = 'translate parameters'

        # Add all the ParameterSpecification objects.
        self._param_specs = self._define_param_specs()

        # Check that there are no duplicated names.
        self._check_self()

    @property
    def group_names(self):
        """Returns the list of parameter group names."""
        return self._group_descriptions.keys()

    def group_description(self, name):
        """Returns the description string for the given group name."""
        return self._group_descriptions[name]

    def params_by_group(self, group_name):
        """Returns the list of ParameterSpecifications for the given group."""
        return self._param_specs[group_name]

    def _define_param_specs(self):
        """Adds all ParameterSpecification objects."""
        param_specs = {}

        # Add an empty list for each parameter group.
        for group in self.group_names:
            param_specs[group] = []

        # Add non-command-line parameters.

        group = param_specs['']

        group.append(ParameterSpecification(
            name='model_version', default=None,
            derivation_func=_derive_model_version))

        group.append(ParameterSpecification(
            name='theano_compat', default=None,
            derivation_func=lambda _, meta_config: meta_config.from_theano))

        group.append(ParameterSpecification(
            name='source_dicts', default=None,
            derivation_func=lambda config, _: config.dictionaries[:-1]))

        group.append(ParameterSpecification(
            name='target_dict', default=None,
            derivation_func=lambda config, _: config.dictionaries[-1]))

        group.append(ParameterSpecification(
            name='target_embedding_size', default=None,
            derivation_func=_derive_target_embedding_size))

        # All remaining parameters are command-line parameters.

        # Add command-line parameters for the 'data' group.

        group = param_specs['data']

        group.append(ParameterSpecification(
            name='source_dataset', default=None,
            visible_arg_names=['--source_dataset'],
            derivation_func=_derive_source_dataset,
            type=str, metavar='PATH',
            help='parallel training corpus (source)'))

        group.append(ParameterSpecification(
            name='target_dataset', default=None,
            visible_arg_names=['--target_dataset'],
            derivation_func=_derive_target_dataset,
            type=str, metavar='PATH',
            help='parallel training corpus (target)'))

        # Hidden option for backward compatibility.
        group.append(ParameterSpecification(
            name='datasets', default=None,
            visible_arg_names=[], hidden_arg_names=['--datasets'],
            type=str, metavar='PATH', nargs=2))

        group.append(ParameterSpecification(
            name='dictionaries', default=None,
            visible_arg_names=['--dictionaries'], hidden_arg_names=[],
            type=str, required=True, metavar='PATH', nargs='+',
            help='network vocabularies (one per source factor, plus target '
                 'vocabulary)'))

        group.append(ParameterSpecification(
            name='saveFreq', default=30000,
            visible_arg_names=['--saveFreq'],
            type=int, metavar='INT',
            help='save frequency (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='saveto', default='model',
            visible_arg_names=['--model', '--saveto'],
            type=str, metavar='PATH',
            help='model file name (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='reload', default=None,
            visible_arg_names=['--reload'],
            type=str, metavar='PATH',
            help='load existing model from this path. Set to '
                 '"latest_checkpoint" to reload the latest checkpoint in the '
                 'same directory of --model'))

        group.append(ParameterSpecification(
            name='reload_training_progress', default=True,
            visible_arg_names=['--no_reload_training_progress'],
            action='store_false',
            help='don\'t reload training progress (only used if --reload '
                 'is enabled)'))

        group.append(ParameterSpecification(
            name='summary_dir', default=None,
            visible_arg_names=['--summary_dir'],
            type=str, metavar='PATH',
            help='directory for saving summaries (default: same directory '
                 'as the --model file)'))

        group.append(ParameterSpecification(
            name='summaryFreq', default=0,
            visible_arg_names=['--summaryFreq'],
            type=int, metavar='INT',
            help='Save summaries after INT updates, if 0 do not save '
                 'summaries (default: %(default)s)'))

        # Add command-line parameters for 'network' group.

        group = param_specs['network']

        group.append(ParameterSpecification(
            name='embedding_size', default=512,
            legacy_names=['dim_word'],
            visible_arg_names=['--embedding_size', '--dim_word'],
            type=int, metavar='INT',
            help='embedding layer size (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='state_size', default=1000,
            legacy_names=['dim'],
            visible_arg_names=['--state_size', '--dim'],
            type=int, metavar='INT',
            help='hidden state size (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='source_vocab_sizes', default=None,
            visible_arg_names=['--source_vocab_sizes', '--n_words_src'],
            derivation_func=_derive_source_vocab_sizes,
            type=int, metavar='INT', nargs='+',
            help='source vocabulary sizes (one per input factor) (default: '
                 '%(default)s)'))

        group.append(ParameterSpecification(
            name='target_vocab_size', default=-1,
            legacy_names=['n_words'],
            visible_arg_names=['--target_vocab_size', '--n_words'],
            derivation_func=_derive_target_vocab_size,
            type=int, metavar='INT',
            help='target vocabulary size (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='factors', default=1,
            visible_arg_names=['--factors'],
            type=int, metavar='INT',
            help='number of input factors (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='dim_per_factor', default=None,
            visible_arg_names=['--dim_per_factor'],
            derivation_func=_derive_dim_per_factor,
            type=int, metavar='INT', nargs='+',
            help='list of word vector dimensionalities (one per factor): '
                 '\'--dim_per_factor 250 200 50\' for total dimensionality '
                 'of 500 (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='enc_depth', default=1,
            visible_arg_names=['--enc_depth'],
            type=int, metavar='INT',
            help='number of encoder layers (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='enc_recurrence_transition_depth', default=1,
            visible_arg_names=['--enc_recurrence_transition_depth'],
            type=int, metavar='INT',
            help='number of GRU transition operations applied in the '
                 'encoder. Minimum is 1. (Only applies to gru). (default: '
                 '%(default)s)'))

        group.append(ParameterSpecification(
            name='dec_depth', default=1,
            visible_arg_names=['--dec_depth'],
            type=int, metavar='INT',
            help='number of decoder layers (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='dec_base_recurrence_transition_depth', default=2,
            visible_arg_names=['--dec_base_recurrence_transition_depth'],
            type=int, metavar='INT',
            help='number of GRU transition operations applied in the first '
                 'layer of the decoder. Minimum is 2.  (Only applies to '
                 'gru_cond). (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='dec_high_recurrence_transition_depth', default=1,
            visible_arg_names=['--dec_high_recurrence_transition_depth'],
            type=int, metavar='INT',
            help='number of GRU transition operations applied in the higher '
                 'layers of the decoder. Minimum is 1. (Only applies to '
                 'gru). (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='dec_deep_context', default=False,
            visible_arg_names=['--dec_deep_context'],
            action='store_true',
            help='pass context vector (from first layer) to deep decoder '
                 'layers'))

        group.append(ParameterSpecification(
            name='use_dropout', default=False,
            visible_arg_names=['--use_dropout'],
            action="store_true",
            help='use dropout layer (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='dropout_embedding', default=None,
            visible_arg_names=['--dropout_embedding'],
            derivation_func=_derive_dropout_embedding,
            type=float, metavar="FLOAT",
            help='dropout for input embeddings (0: no dropout) (default: '
                 '%(default)s)'))

        group.append(ParameterSpecification(
            name='dropout_hidden', default=None,
            visible_arg_names=['--dropout_hidden'],
            derivation_func=_derive_dropout_hidden,
            type=float, metavar="FLOAT",
            help='dropout for hidden layer (0: no dropout) (default: '
                 '%(default)s)'))

        group.append(ParameterSpecification(
            name='dropout_source', default=0.0,
            visible_arg_names=['--dropout_source'],
            type=float, metavar='FLOAT',
            help='dropout source words (0: no dropout) (default: '
                 '%(default)s)'))

        group.append(ParameterSpecification(
            name='dropout_target', default=0.0,
            visible_arg_names=['--dropout_target'],
            type=float, metavar='FLOAT',
            help='dropout target words (0: no dropout) (default: '
                 '%(default)s)'))

        group.append(ParameterSpecification(
            name='use_layer_norm', default=False,
            legacy_names=['layer_normalisation'],
            visible_arg_names=['--use_layer_norm', '--layer_normalisation'],
            action='store_true',
            help='Set to use layer normalization in encoder and decoder'))

        group.append(ParameterSpecification(
            name='tie_encoder_decoder_embeddings', default=False,
            visible_arg_names=['--tie_encoder_decoder_embeddings'],
            action='store_true',
            help='tie the input embeddings of the encoder and the decoder '
                 '(first factor only). Source and target vocabulary size '
                 'must be the same'))

        group.append(ParameterSpecification(
            name='tie_decoder_embeddings', default=False,
            visible_arg_names=['--tie_decoder_embeddings'],
            action='store_true',
            help='tie the input embeddings of the decoder with the softmax '
                 'output embeddings'))

        group.append(ParameterSpecification(
            name='output_hidden_activation', default='tanh',
            visible_arg_names=['--output_hidden_activation'],
            type=str, choices=['tanh', 'relu', 'prelu', 'linear'],
            help='activation function in hidden layer of the output '
                 'network (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='softmax_mixture_size', default=1,
            visible_arg_names=['--softmax_mixture_size'],
            type=int, metavar='INT',
            help='number of softmax components to use (default: %(default)s)'))

        # Add command-line parameters for 'training' group.

        group = param_specs['training']

        group.append(ParameterSpecification(
            name='maxlen', default=100,
            visible_arg_names=['--maxlen'],
            type=int, metavar='INT',
            help='maximum sequence length for training and validation '
                 '(default: %(default)s)'))

        group.append(ParameterSpecification(
            name='batch_size', default=80,
            visible_arg_names=['--batch_size'],
            type=int, metavar='INT',
            help='minibatch size (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='token_batch_size', default=0,
            visible_arg_names=['--token_batch_size'],
            type=int, metavar='INT',
            help='minibatch size (expressed in number of source or target '
                 'tokens). Sentence-level minibatch size will be dynamic. If '
                 'this is enabled, batch_size only affects sorting by '
                 'length. (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='max_epochs', default=5000,
            visible_arg_names=['--max_epochs'],
            type=int, metavar='INT',
            help='maximum number of epochs (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='finish_after', default=10000000,
            visible_arg_names=['--finish_after'],
            type=int, metavar='INT',
            help='maximum number of updates (minibatches) (default: '
                 '%(default)s)'))

        group.append(ParameterSpecification(
            name='decay_c', default=0.0,
            visible_arg_names=['--decay_c'],
            type=float, metavar='FLOAT',
            help='L2 regularization penalty (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='map_decay_c', default=0.0,
            visible_arg_names=['--map_decay_c'],
            type=float, metavar='FLOAT',
            help='MAP-L2 regularization penalty towards original weights '
                 '(default: %(default)s)'))

        group.append(ParameterSpecification(
            name='prior_model', default=None,
            visible_arg_names=['--prior_model'],
            type=str, metavar='PATH',
            help='Prior model for MAP-L2 regularization. Unless using '
                 '\"--reload\", this will also be used for initialization.'))

        group.append(ParameterSpecification(
            name='clip_c', default=1.0,
            visible_arg_names=['--clip_c'],
            type=float, metavar='FLOAT',
            help='gradient clipping threshold (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='label_smoothing', default=0.0,
            visible_arg_names=['--label_smoothing'],
            type=float, metavar='FLOAT',
            help='label smoothing (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='shuffle_each_epoch', default=True,
            visible_arg_names=['--no_shuffle'],
            action='store_false',
            help='disable shuffling of training data (for each epoch)'))

        group.append(ParameterSpecification(
            name='keep_train_set_in_memory', default=False,
            visible_arg_names=['--keep_train_set_in_memory'],
            action='store_true',
            help='Keep training dataset lines stores in RAM during training'))

        group.append(ParameterSpecification(
            name='sort_by_length', default=True,
            visible_arg_names=['--no_sort_by_length'],
            action='store_false',
            help='do not sort sentences in maxibatch by length'))

        group.append(ParameterSpecification(
            name='maxibatch_size', default=20,
            visible_arg_names=['--maxibatch_size'],
            type=int, metavar='INT',
            help='size of maxibatch (number of minibatches that are sorted '
                 'by length) (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='optimizer', default='adam',
            visible_arg_names=['--optimizer'],
            type=str, choices=['adam'],
            help='optimizer (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='learning_rate', default=0.0001,
            visible_arg_names=['--learning_rate', '--lrate'],
            legacy_names=['lrate'],
            type=float, metavar='FLOAT',
            help='learning rate (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='adam_beta1', default=0.9,
            visible_arg_names=['--adam_beta1'],
            type=float, metavar='FLOAT',
            help='exponential decay rate for the first moment estimates '
                 '(default: %(default)s)'))

        group.append(ParameterSpecification(
            name='adam_beta2', default=0.999,
            visible_arg_names=['--adam_beta2'],
            type=float, metavar='FLOAT',
            help='exponential decay rate for the second moment estimates '
                 '(default: %(default)s)'))

        group.append(ParameterSpecification(
            name='adam_epsilon', default=1e-08,
            visible_arg_names=['--adam_epsilon'],
            type=float, metavar='FLOAT',
            help='constant for numerical stability (default: %(default)s)'))

        # Add command-line parameters for 'validation' group.

        group = param_specs['validation']

        group.append(ParameterSpecification(
            name='valid_source_dataset', default=None,
            visible_arg_names=['--valid_source_dataset'],
            derivation_func=_derive_valid_source_dataset,
            type=str, metavar='PATH',
            help='source validation corpus (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='valid_target_dataset', default=None,
            visible_arg_names=['--valid_target_dataset'],
            derivation_func=_derive_valid_target_dataset,
            type=str, metavar='PATH',
            help='target validation corpus (default: %(default)s)'))

        # Hidden option for backward compatibility.
        group.append(ParameterSpecification(
            name='valid_datasets', default=None,
            hidden_arg_names=['--valid_datasets'],
            type=str, metavar='PATH', nargs=2))

        group.append(ParameterSpecification(
            name='valid_batch_size', default=80,
            visible_arg_names=['--valid_batch_size'],
            type=int, metavar='INT',
            help='validation minibatch size (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='valid_token_batch_size', default=0,
            visible_arg_names=['--valid_token_batch_size'],
            type=int, metavar='INT',
            help='validation minibatch size (expressed in number of source '
                 'or target tokens). Sentence-level minibatch size will be '
                 'dynamic. If this is enabled, valid_batch_size only affects '
                 'sorting by length. (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='validFreq', default=10000,
            visible_arg_names=['--validFreq'],
            type=int, metavar='INT',
            help='validation frequency (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='valid_script', default=None,
            visible_arg_names=['--valid_script'],
            type=str, metavar='PATH',
            help='path to script for external validation (default: '
                 '%(default)s). The script will be passed an argument '
                 'specifying the path of a file that contains translations '
                 'of the source validation corpus. It must write a single '
                 'score to standard output.'))

        group.append(ParameterSpecification(
            name='patience', default=10,
            visible_arg_names=['--patience'],
            type=int, metavar='INT',
            help='early stopping patience (default: %(default)s)'))

        # Add command-line parameters for 'display' group.

        group = param_specs['display']

        group.append(ParameterSpecification(
            name='dispFreq', default=1000,
            visible_arg_names=['--dispFreq'],
            type=int, metavar='INT',
            help='display loss after INT updates (default: %(default)s)'))

        group.append(ParameterSpecification(
            name='sampleFreq', default=10000,
            visible_arg_names=['--sampleFreq'],
            type=int, metavar='INT',
            help='display some samples after INT updates (default: '
                 '%(default)s)'))

        group.append(ParameterSpecification(
            name='beamFreq', default=10000,
            visible_arg_names=['--beamFreq'],
            type=int, metavar='INT',
            help='display some beam_search samples after INT updates '
                 '(default: %(default)s)'))

        group.append(ParameterSpecification(
            name='beam_size', default=12,
            visible_arg_names=['--beam_size'],
            type=int, metavar='INT',
            help='size of the beam (default: %(default)s)'))

        # Add command-line parameters for 'translate' group.

        group = param_specs['translate']

        group.append(ParameterSpecification(
            name='normalize', default=True,
            visible_arg_names=['--no_normalize'],
            action='store_false',
            help='Cost of sentences will not be normalized by length'))

        group.append(ParameterSpecification(
            name='n_best', default=False,
            visible_arg_names=['--n_best'],
            action='store_true', dest='n_best',
            help='Print full beam'))

        group.append(ParameterSpecification(
            name='translation_maxlen', default=200,
            visible_arg_names=['--translation_maxlen'],
            type=int, metavar='INT',
            help='Maximum length of translation output sentence (default: '
                 '%(default)s)'))

        return param_specs

    def _check_self(self):
        # Check that there are no duplicated parameter names.
        param_names = set()
        for group in self.group_names:
            for param in self.params_by_group(group):
                assert param.name not in param_names
                param_names.add(param.name)
                for name in param.legacy_names:
                    assert name not in param_names
                    param_names.add(name)
        # Check that there are no duplicated command-line argument names.
        arg_names = set()
        for group in self.group_names:
            for param in self.params_by_group(group):
                for arg_list in (param.visible_arg_names,
                                 param.hidden_arg_names):
                    for name in arg_list:
                        assert name not in arg_names
                        arg_names.add(param.name)


def read_config_from_cmdline():
    """Reads a config from the command-line.

    Logs an error and exits if the parameter values are not mutually
    consistent.

    Returns:
        An argparse.Namespace object representing the config.
    """

    spec = ConfigSpecification()
    config = argparse.Namespace()

    # Set meta parameters.
    meta_config = argparse.Namespace()
    meta_config.from_cmdline = True
    meta_config.from_theano = False

    # Set non-command-line parameters to default values.
    for param in spec.params_by_group(""):
        if param.visible_arg_names == [] and param.hidden_arg_names == []:
            setattr(config, param.name, param.default)

    # Construct an ArgumentParser and parse command-line args.
    parser = argparse.ArgumentParser()
    for group_name in spec.group_names:
        if group_name == "":
            target = parser
        else:
            description = spec.group_description(group_name)
            target = parser.add_argument_group(description)

        for param in spec.params_by_group(group_name):
            if param.visible_arg_names == [] and param.hidden_arg_names == []:
                # Internal parameter - no command-line argument.
                continue
            argparse_args = dict(param.argparse_args)
            argparse_args['dest'] = param.name
            if param.visible_arg_names == []:
                argparse_args['help'] = argparse.SUPPRESS
                target.add_argument(*param.hidden_arg_names, **argparse_args)
                continue
            if 'required' in argparse_args and argparse_args['required']:
                mutex_group = \
                    target.add_mutually_exclusive_group(required=True)
                del argparse_args['required']
            else:
                mutex_group = target.add_mutually_exclusive_group()
            mutex_group.add_argument(*param.visible_arg_names, **argparse_args)
            # Add any hidden arguments for this param.
            if len(param.hidden_arg_names) > 0:
                argparse_args['help'] = argparse.SUPPRESS
                mutex_group.add_argument(*param.hidden_arg_names,
                                         **argparse_args)
    config = parser.parse_args(namespace=config)

    # Perform consistency checks.
    error_messages = _check_config_consistency(config)
    if len(error_messages) > 0:
        for msg in error_messages:
            logging.error(msg)
        sys.exit(1)

    # Run derivation functions.
    for group in spec.group_names:
        for param in spec.params_by_group(group):
            if param.derivation_func is not None:
                setattr(config, param.name,
                        param.derivation_func(config, meta_config))

    return config


def load_config_from_json_file(basename):
    """Loads and, if necessary, updates a config from a JSON (or Pickle) file.

    Logs an error and exits if the file can't be loaded.

    Args:
        basename: a string containing the path to the corresponding model file.

    Returns:
        An argparse.Namespace object representing the config.
    """

    spec = ConfigSpecification()

    # Load a config from a JSON (or Pickle) config file.
    try:
        with open('%s.json' % basename, 'rb') as f:
            config_as_dict = json.load(f)
    except:
        try:
            with open('%s.pkl' % basename, 'rb') as f:
                config_as_dict = pkl.load(f)
        except:
            logging.error('config file {}.json is missing'.format(basename))
            sys.exit(1)
    config = argparse.Namespace(**config_as_dict)

    # Set meta parameters.
    meta_config = argparse.Namespace()
    meta_config.from_cmdline = False
    meta_config.from_theano = (not hasattr(config, 'embedding_size'))

    # Update config to use current parameter names.
    for group_name in spec.group_names:
        for param in spec.params_by_group(group_name):
            for legacy_name in param.legacy_names:
                # TODO It shouldn't happen, but check for multiple names
                #      (legacy and/or current) for same parameter appearing
                #      in config.
                if hasattr(config, legacy_name):
                    val = getattr(config, legacy_name)
                    assert not hasattr(config, param.name)
                    setattr(config, param.name, val)
                    delattr(config, legacy_name)

    # Add missing parameters.
    for group_name in spec.group_names:
        for param in spec.params_by_group(group_name):
            if not hasattr(config, param.name):
                setattr(config, param.name, param.default)

    # Run derivation functions.
    for group in spec.group_names:
        for param in spec.params_by_group(group):
            if param.derivation_func is not None:
                setattr(config, param.name,
                        param.derivation_func(config, meta_config))

    return config


def _check_config_consistency(config):
    """Performs consistency checks on a config read from the command-line.

    Args:
        config: an argparse.Namespace object.

    Returns:
        A list of error messages, one for each check that failed. An empty
        list indicates that all checks passed.
    """
    error_messages = []

    if config.datasets:
        if config.source_dataset or config.target_dataset:
            msg = 'argument clash: --datasets is mutually exclusive ' \
                  'with --source_dataset and --target_dataset'
            error_messages.append(msg)
    elif not config.source_dataset:
        msg = '--source_dataset is required'
        error_messages.append(msg)
    elif not config.target_dataset:
        msg = '--target_dataset is required'
        error_messages.append(msg)

    if config.valid_datasets:
        if config.valid_source_dataset or config.valid_target_dataset:
            msg = 'argument clash: --valid_datasets is mutually ' \
                  'exclusive with --valid_source_dataset and ' \
                  '--valid_target_dataset'
            error_messages.append(msg)

    if (config.source_vocab_sizes is not None and
            len(config.source_vocab_sizes) > config.factors):
        msg = 'too many values supplied to \'--source_vocab_sizes\' option ' \
              '(expected one per factor = {})'.format(config.factors)
        error_messages.append(msg)

    if config.dim_per_factor is None and config.factors != 1:
        msg = 'if using factored input, you must specify \'dim_per_factor\''
        error_messages.append(msg)

    if config.dim_per_factor is not None:
        if len(config.dim_per_factor) != config.factors:
            msg = 'mismatch between \'--factors\' ({0}) and ' \
                  '\'--dim_per_factor\' ({1} entries)'.format(
                      config.factors, len(config.dim_per_factor))
            error_messages.append(msg)
        elif sum(config.dim_per_factor) != config.embedding_size:
            msg = 'mismatch between \'--embedding_size\' ({0}) and ' \
                  '\'--dim_per_factor\' (sums to {1})\''.format(
                      config.embedding_size, sum(config.dim_per_factor))
            error_messages.append(msg)

    if len(config.dictionaries) != config.factors + 1:
        msg = '\'--dictionaries\' must specify one dictionary per source ' \
              'factor and one target dictionary'
        error_messages.append(msg)

    return error_messages


def _derive_model_version(config, meta_config):
    if meta_config.from_cmdline:
        # We're creating a new model - set the current version number.
        return 0.2
    if config.model_version is not None:
        return config.model_version
    if meta_config.from_theano and config.use_dropout:
        logging.error('version 0 dropout is not supported in '
                      'TensorFlow Nematus')
        sys.exit(1)
    return 0.1


def _derive_target_embedding_size(config, meta_config):
    assert hasattr(config, 'embedding_size')
    if not config.tie_encoder_decoder_embeddings:
        return config.embedding_size
    if config.factors > 1:
        assert hasattr(config, 'dim_per_factor')
        assert config.dim_per_factor is not None
        return config.dim_per_factor[0]
    else:
        return config.embedding_size


def _derive_source_dataset(config, meta_config):
    if config.source_dataset is not None:
        return config.source_dataset
    assert config.datasets is not None
    return config.datasets[0]


def _derive_target_dataset(config, meta_config):
    if config.target_dataset is not None:
        return config.target_dataset
    assert config.datasets is not None
    return config.datasets[1]


def _derive_source_vocab_sizes(config, meta_config):
    if config.source_vocab_sizes is not None:
        if len(config.source_vocab_sizes) == config.factors:
            # Case 1: we're loading parameters from a recent config or
            #         we're processing command-line arguments and
            #         a source_vocab_sizes was fully specified.
            return config.source_vocab_sizes
        else:
            # Case 2: source_vocab_sizes was given on the command-line
            #         but was only partially specified
            assert meta_config.from_cmdline
            assert len(config.source_vocab_sizes) < config.factors
            num_missing = config.factors - len(config.source_vocab_sizes)
            vocab_sizes = config.source_vocab_sizes + [-1] * num_missing
    elif hasattr(config, 'n_words_src'):
        # Case 3: we're loading parameters from a Theano config.
        #         This will always contain a single value for the
        #         source vocab size regardless of how many factors
        #         there are.
        assert not meta_config.from_cmdline
        assert meta_config.from_theano
        assert type(config.n_words_src) == int
        return [config.n_words_src] * config.factors
    elif hasattr(config, 'source_vocab_size'):
        # Case 4: we're loading parameters from a pre-factors
        #         TensorFlow config.
        assert not meta_config.from_cmdline
        assert not meta_config.from_theano
        assert config.factors == 1
        return [config.source_vocab_size]
    else:
        # Case 5: we're reading command-line parameters and
        #         --source_vocab_size was not given.
        assert meta_config.from_cmdline
        vocab_sizes = [-1] * config.factors
    # For any unspecified vocabulary sizes, determine sizes from the
    # vocabulary dictionaries.
    for i, vocab_size in enumerate(vocab_sizes):
        if vocab_size >= 0:
            continue
        path = config.dictionaries[i]
        vocab_sizes[i] = _determine_vocab_size_from_file(path)
    return vocab_sizes


def _derive_target_vocab_size(config, meta_config):
    if config.target_vocab_size != -1:
        return config.target_vocab_size
    path = config.dictionaries[-1]
    return _determine_vocab_size_from_file(path)


def _derive_dim_per_factor(config, meta_config):
    if config.dim_per_factor is not None:
        return config.dim_per_factor
    assert config.factors == 1
    return [config.embedding_size]


def _derive_dropout_embedding(config, meta_config):
    if config.dropout_embedding is not None:
        return config.dropout_embedding
    return 0.2 if meta_config.from_cmdline else 0.0


def _derive_dropout_hidden(config, meta_config):
    if config.dropout_hidden is not None:
        return config.dropout_hidden
    return 0.2 if meta_config.from_cmdline else 0.0


def _derive_valid_source_dataset(config, meta_config):
    if config.valid_source_dataset is not None:
        return config.valid_source_dataset
    assert config.valid_datasets is not None
    return config.valid_datasets[0]


def _derive_valid_target_dataset(config, meta_config):
    if config.valid_target_dataset is not None:
        return config.valid_target_dataset
    assert config.valid_datasets is not None
    return config.valid_datasets[1]


def _determine_vocab_size_from_file(path):
    try:
        d = util.load_dict(path)
    except IOError as x:
        logging.error('failed to determine vocabulary size from file: '
                      '{}: {}'.format(path, str(x)))
        sys.exit(1)
    except:
        logging.error('failed to determine vocabulary size from file: '
                      '{}'.format(path))
        sys.exit(1)
    return max(d.values()) + 1
