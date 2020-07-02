
import sys

import tensorflow as tf
from tqdm import tqdm

from tfnlu.utils.logger import logger
from tfnlu.utils.tfnlu_model import TFNLUModel
from tfnlu.utils.config import MAX_LENGTH, DEFAULT_BATCH_SIZE
from .get_tags import get_tags
from .tagger_model import TaggerModel
from .score_table import score_table
from .check_validation import CheckValidation


class Tagger(TFNLUModel):
    def __init__(self,
                 encoder_path,
                 encoder_trainable=False):

        super(Tagger, self).__init__()

        self.encoder_path = encoder_path
        self.encoder_trainable = encoder_trainable

        self.model = None
        self.word_index = None
        self.index_word = None

        self.tto = None

    def evaluate_table(self, x, y, batch_size=DEFAULT_BATCH_SIZE):
        preds = self.predict(x, batch_size=batch_size)
        return score_table(preds, y)

    def check_data(self, x, y=None, batch_size=DEFAULT_BATCH_SIZE):
        if not isinstance(batch_size, int):
            batch_size = DEFAULT_BATCH_SIZE
        assert hasattr(x, '__len__'), 'X should be a array like'
        assert len(x) > 0, 'len(X) should more than 0'
        assert isinstance(x[0], (tuple, list)), \
            'Elements of X should be tuple or list'
        if y is not None:
            assert len(x) == len(y), 'len(X) must equal to len(y)'
            assert isinstance(y[0], (tuple, list)), \
                'Elements of y should be tuple or list'
            # 暂时关掉，因为X可能有 [MASK] 等特殊符号
            assert len(x[0]) == len(y[0]), \
                'Lengths of each elements in X should as same as Y'
        for xx in x:
            if len(xx) > MAX_LENGTH:
                logger.warn(
                    f'Some sample(s) longer than {MAX_LENGTH}, will be cut')
                break

        def _make_gen(x, y=None):
            def _gen_x():
                for xx in x:
                    xx = tf.constant(
                        ['[CLS]'] + xx[:MAX_LENGTH] + ['[SEP]'],
                        tf.string)
                    yield xx

            def _gen_xy():
                for xx, yy in zip(x, y):
                    xx = tf.constant(
                        ['[CLS]'] + xx[:MAX_LENGTH] + ['[SEP]'],
                        tf.string)
                    yy = tf.constant(
                        ['[CLS]'] + yy[:MAX_LENGTH] + ['[SEP]'],
                        tf.string)
                    yield xx, yy

            if y is None:
                return {
                    'generator': _gen_x,
                    'output_types': tf.string,
                    'output_shapes': tf.TensorShape([None, ])}
            return {
                    'generator': _gen_xy,
                    'output_types': (tf.string, tf.string),
                    'output_shapes': (
                        tf.TensorShape([None, ]),
                        tf.TensorShape([None, ]))}

        def size_xy(x, y):
            return tf.size(x)

        def size_x(x):
            return tf.size(x)

        dataset = tf.data.Dataset.from_generator(**_make_gen(x, y))
        bucket_boundaries = list(range(MAX_LENGTH // 10, MAX_LENGTH, 50))
        dataset = dataset.apply(
            tf.data.experimental.bucket_by_sequence_length(
                size_xy if y is not None else size_x,
                bucket_batch_sizes=[batch_size] * (len(bucket_boundaries) + 1),
                bucket_boundaries=bucket_boundaries
            )
        )
        dataset = dataset.cache()
        dataset = dataset.prefetch(tf.data.experimental.AUTOTUNE)

        return dataset

    def fit(self,
            x,
            y,
            epochs=1,
            batch_size=DEFAULT_BATCH_SIZE,
            shuffle=True,
            validation_data=None,
            save_best=None,
            optimizer=None):
        data = self.check_data(x, y, batch_size)
        if not self.model:
            logger.info('build model')
            word_index, index_word = get_tags(y)
            logger.info(f'tags count: {len(word_index)}')
            logger.info('build model')
            self.model = TaggerModel(
                encoder_path=self.encoder_path,
                word_index=word_index,
                index_word=index_word,
                encoder_trainable=self.encoder_trainable)
            self.model._set_inputs(
                tf.keras.backend.placeholder((None, None), dtype='string'))

        self.model.compile(optimizer=(
            optimizer
            if optimizer is not None
            else tf.keras.optimizers.Adam(1e-4)))

        logger.info('check model predict')
        pred_data = self.check_data(x, y=None, batch_size=batch_size)
        for xx in pred_data.take(2):
            self.model.predict_on_batch(xx)

        logger.info('start training')

        self.model.fit(
            data,
            epochs=epochs,
            callbacks=[
                CheckValidation(
                    tagger=self,
                    batch_size=batch_size,
                    validation_data=validation_data,
                    save_best=save_best)
            ]
        )
        logger.info('training done')

    def predict(self, x, batch_size=DEFAULT_BATCH_SIZE, verbose=1):
        assert self.model is not None, 'model not fit or load'
        pred = []

        total_batch = int((len(x) - 1) / batch_size) + 1
        pbar = range(total_batch)
        if verbose:
            pbar = tqdm(pbar, file=sys.stdout)
        for i in pbar:
            x_batch = x[i * batch_size:(i + 1) * batch_size]
            x_batch = [
                ['[CLS]'] + xx + ['[SEP]']
                for xx in x_batch
            ]
            x_batch = tf.ragged.constant(x_batch).to_tensor()
            p = self.model(x_batch)
            pred += [
                [token.decode('UTF-8') for token in sent]
                for sent in p.numpy().tolist()
            ]
        pred = [
            ip[:len(ix)]
            for ip, ix in zip(pred, x)
        ]
        return pred
