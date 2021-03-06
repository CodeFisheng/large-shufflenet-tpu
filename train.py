import tensorflow as tf
from model import model_fn, RestoreMovingAverageHook
from input_pipeline import Pipeline

from tensorflow.contrib.tpu.python.tpu import tpu_config
from tensorflow.contrib.tpu.python.tpu import tpu_estimator

tf.logging.set_verbosity(tf.logging.DEBUG)


"""
The purpose of this script is to train a network.
Evaluation will happen periodically.

To use it just run:
python train.py
"""

# 1281144/1024 = 1251.12
# so 1 epoch = 1251 steps

TPU = 'codefisheng'
TPU_ZONE = 'us-central1-b'
GCP_PROJECT = 'resnet-tpu-bf16'

BATCH_SIZE = 1024
VALIDATION_BATCH_SIZE = 1024  # some images will be excluded
NUM_EPOCHS =200
TRAIN_DATASET_SIZE = 1281144
VAL_DATASET_SIZE = 1281144 #49999
NUM_STEPS_PER_EPOCH = TRAIN_DATASET_SIZE // BATCH_SIZE
NUM_STEPS = NUM_EPOCHS * NUM_STEPS_PER_EPOCH  # 250200
EXTRA_STEPS = 12510

STEPS_PER_EVAL = 1251  # evaluate after every fourth epoch

# number of steps to run on TPU before outfeeding metrics to the CPU
ITERATIONS_PER_LOOP = 1251

# whether to do mixed precision training
HALF_PRECISION = False

NUM_WARM_UP_STEPS = 5 * NUM_STEPS_PER_EPOCH

PARAMS = {
    'train_file_pattern': 'gs://hengshi-tpu/shufflenet_data_dir/train_shards/shard-*',
    'val_file_pattern': 'gs://hengshi-tpu/shufflenet_data_dir/val_shards/shard-*',
    'model_dir': 'gs://hengshi-tpu/model_tmp_dir',#'gs://hengshi-tpu/sn_model_dir_large_bf16/',

    'num_classes': 1000,
    'depth_multiplier': '1.5',
    'weight_decay': 4e-5,

    'global_batch_size': BATCH_SIZE,

    'warm_up_steps': NUM_WARM_UP_STEPS,
    'warm_up_lr': 1e-6,

    # linear learning rate schedule
    'initial_learning_rate': 0.5,
    'decay_steps': NUM_STEPS - NUM_WARM_UP_STEPS,
    'end_learning_rate': 1e-8,

    'iterations_per_loop': ITERATIONS_PER_LOOP,
    'use_bfloat16': HALF_PRECISION
}


def get_input_fn(is_training, image_size=None):

    file_pattern = PARAMS['train_file_pattern'] if is_training else PARAMS['val_file_pattern']

    def input_fn(params):
        pipeline = Pipeline(
            file_pattern, is_training, image_size=image_size,
            batch_size=params['batch_size'],
            use_bfloat16=PARAMS['use_bfloat16']
        )
        return pipeline.dataset

    return input_fn


tpu_cluster_resolver = tf.contrib.cluster_resolver.TPUClusterResolver(
    TPU, zone=TPU_ZONE,
    project=GCP_PROJECT
)
config = tpu_config.RunConfig(
    cluster=tpu_cluster_resolver,
    model_dir=PARAMS['model_dir'],
    save_checkpoints_steps=ITERATIONS_PER_LOOP,
    keep_checkpoint_max=5,
    tpu_config=tpu_config.TPUConfig(
        num_shards=8,
        iterations_per_loop=ITERATIONS_PER_LOOP,
        per_host_input_for_training=tf.contrib.tpu.InputPipelineConfig.PER_HOST_V2
    ),
)

estimator = tpu_estimator.TPUEstimator(
    model_fn=model_fn, model_dir=PARAMS['model_dir'],
    params=PARAMS, config=config,
    train_batch_size=BATCH_SIZE,
    eval_batch_size=VALIDATION_BATCH_SIZE
)


train_input_fn = get_input_fn(is_training=True, image_size=224)
eval_input_fn = get_input_fn(is_training=False)  # for validation we also use size 224


def load_global_step_from_checkpoint_dir(checkpoint_dir):
    try:
        checkpoint_reader = tf.train.NewCheckpointReader(
            tf.train.latest_checkpoint(checkpoint_dir)
        )
        return int(checkpoint_reader.get_tensor(tf.GraphKeys.GLOBAL_STEP))
    except:
        return 0


def train_and_eval(input_fn, end_step):

    # load last checkpoint and start from there
    current_step = load_global_step_from_checkpoint_dir(PARAMS['model_dir'])

    while current_step < end_step:

        next_checkpoint = min(current_step + STEPS_PER_EVAL, end_step)
        estimator.train(input_fn=input_fn, max_steps=next_checkpoint)
        current_step = next_checkpoint

        tf.logging.info('Starting to evaluate.')
        eval_results = estimator.evaluate(
            # TODO eval_input_fn to input_fn
            input_fn=input_fn,
            steps=VAL_DATASET_SIZE // VALIDATION_BATCH_SIZE,
            hooks=[RestoreMovingAverageHook(PARAMS['model_dir'])]
        )
        tf.logging.info('Eval results at step %d: %s', next_checkpoint, eval_results)


train_and_eval(train_input_fn, 1)#NUM_EPOCHS * NUM_STEPS_PER_EPOCH + EXTRA_STEPS)
