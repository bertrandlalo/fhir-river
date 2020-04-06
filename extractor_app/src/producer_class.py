#!/usr/bin/env python

from confluent_kafka import Producer
from extractor_app.src.config.logger import create_logger

logger = create_logger('extractor_sql')


def callback_fn(err, msg, obj):
    """
    Handle delivery reports served from producer.poll.
    This callback takes an extra argument, obj.
    This allows the original contents to be included for debugging purposes.
    """
    if err is not None:
        logger.error('Message {} delivery failed with error {} for topic {}'.format(
            obj, err, msg.topic()))
    else:
        logger.error("Event Successfully created")


class ExtractorProducer:

    def __init__(self,
                 broker=None,
                 callback_function=None):
        """
        Instantiate the class and create the consumer object
        :param broker: host[:port]’ string (or list of ‘host[:port]’ strings) that the consumer should contact to
        bootstrap initial cluster metadata
        :param registry: string, avro registry url
        :param callback_function: fn taking 3 args: err, msg, obj, that is called after the event is produced
        and an error increment (int)
        """
        self.broker = broker
        self.partition = 0
        self.callback_function = callback_function

        # Create consumer
        self.producer = Producer(self._generate_config())

    def _generate_config(self):
        """
        Generate configuration dictionary for consumer
        :return:
        """
        config = {'bootstrap.servers': self.broker,
                  'session.timeout.ms': 6000}
        return config

    def produce_event(self, topic, record):
        """
        Produce event in the specified topic
        :return:
        """
        try:
            self.producer.produce(topic=topic,
                                  value=record)
            # callback=lambda err, msg, obj=record: self.callback_function(err, msg, obj))
            self.producer.poll(1)  # Callback function
        except ValueError as error:
            logger.error(error)