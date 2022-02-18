import functools
import logging
import pika
import ssl
import sys
import time
from datetime import datetime

class PubSubConsumer(object):
    """
    Based on https://github.com/pika/pika/blob/master/examples/asynchronous_consumer_example.py

    This is an example consumer that will handle unexpected interactions
    with RabbitMQ such as channel and connection closures.

    If RabbitMQ closes the connection, it will reopen it. You should
    look at the output, as there are limited reasons why the connection may
    be closed, which usually are tied to permission related issues or
    socket timeouts.

    If the channel is closed, it will indicate a problem with one of the
    commands that were issued and that should surface in the output as well.

    """

    def __init__(self, amqp_prefix, logger):
        """Create a new instance of the consumer class, passing in the AMQP
        URL used to connect to RabbitMQ.

        :param str amqp_url: The AMQP url to connect with

        """
        self._connection = None
        self._channel = None
        self._closing = False
        self._consuming = False
        self._consumer_tag = None
        self._prefix = amqp_prefix
        self._timer_id = None
        self._run_until = None
        self.logger = logger

    # to be overwritten dynamically by subclass
    def interval(self):
        return 300

    def restart_timer(self):
        interval = None
        if self._timer_id:
            self._connection.ioloop.remove_timeout(self._timer_id)
            interval = self.interval()
        else:
            # check the initial state on first timer hit
            # so be quick about it
            interval = 0
        self._timer_id = self._connection.ioloop.call_later(interval, self.still_alive)

    def still_alive(self):
        # output something so gocd doesn't consider it stalled
        self.logger.info('Still alive: {}'.format(datetime.now().time()))
        if self._run_until and time.time() > self._run_until:
            self.stop()
        else:
            self.restart_timer()

    def connect(self):
        """This method connects to RabbitMQ, returning the connection handle.
        When the connection is established, the on_connection_open method
        will be invoked by pika.

        :rtype: pika.SelectConnection

        """
        self.logger.info('Connecting to %s', self._prefix)
        account = 'opensuse'
        server = 'rabbit.opensuse.org'
        if self._prefix == 'suse':
            account = 'suse'
            server = 'rabbit.suse.de'
        credentials = pika.PlainCredentials(account, account)
        context = ssl.create_default_context()
        ssl_options = pika.SSLOptions(context, server)
        parameters = pika.ConnectionParameters(server, 5671, '/', credentials, ssl_options=ssl_options, socket_timeout=10)
        return pika.SelectConnection(parameters,
                                     on_open_callback=self.on_connection_open)

    def close_connection(self):
        """This method closes the connection to RabbitMQ."""
        self._consuming = False
        if self._connection.is_closing or self._connection.is_closed:
            self.logger.info('Connection is closing or already closed')
        else:
            self.logger.info('Closing connection')
            self._connection.close()

    def add_on_connection_close_callback(self):
        """This method adds an on close callback that will be invoked by pika
        when RabbitMQ closes the connection to the publisher unexpectedly.

        """
        self.logger.debug('Adding connection close callback')
        self._connection.add_on_close_callback(self.on_connection_closed)

    def on_connection_closed(self, connection, reason):
        """This method is invoked by pika when the connection to RabbitMQ is
        closed unexpectedly. Since it is unexpected, we will reconnect to
        RabbitMQ if it disconnects.

        :param pika.connection.Connection connection: The closed connection obj
        :param int reply_code: The server provided reply_code if given
        :param str reply_text: The server provided reply_text if given

        """
        self._channel = None
        if self._closing:
            self._connection.ioloop.stop()
        else:
            self.logger.warning('Connection closed, reopening in 5 seconds: %s',
                                reason)
            self._connection.ioloop.call_later(5, self.reconnect)

    def on_connection_open(self, unused_connection):
        """This method is called by pika once the connection to RabbitMQ has
        been established. It passes the handle to the connection object in
        case we need it, but in this case, we'll just mark it unused.

        :type unused_connection: pika.SelectConnection

        """
        self.logger.info('Connection opened')
        self.add_on_connection_close_callback()
        self.open_channel()

    def reconnect(self):
        """Will be invoked by the IOLoop timer if the connection is
        closed. See the on_connection_closed method.

        """
        # This is the old connection IOLoop instance, stop its ioloop
        self._connection.ioloop.stop()

        if not self._closing:

            # Create a new connection
            self._connection = self.connect()

            # There is now a new connection, needs a new ioloop to run
            self._connection.ioloop.start()

    def add_on_channel_close_callback(self):
        """This method tells pika to call the on_channel_closed method if
        RabbitMQ unexpectedly closes the channel.

        """
        self.logger.debug('Adding channel close callback')
        self._channel.add_on_close_callback(self.on_channel_closed)

    def on_channel_closed(self, channel, reason):
        """Invoked by pika when RabbitMQ unexpectedly closes the channel.
        Channels are usually closed if you attempt to do something that
        violates the protocol, such as re-declare an exchange or queue with
        different parameters. In this case, we'll close the connection
        to shutdown the object.

        :param pika.channel.Channel: The closed channel
        :param int reply_code: The numeric reason the channel was closed
        :param str reply_text: The text reason the channel was closed

        """
        self.logger.info('Channel %i was closed: %s',
                         channel, reason)
        self._connection.close()

    def on_channel_open(self, channel):
        """This method is invoked by pika when the channel has been opened.
        The channel object is passed in so we can make use of it.

        Since the channel is now open, we'll declare the exchange to use.

        :param pika.channel.Channel channel: The channel object

        """
        self.logger.debug('Channel opened')
        self._channel = channel
        self.add_on_channel_close_callback()
        self.setup_exchange('pubsub')

    def setup_exchange(self, exchange_name):
        """Setup the exchange on RabbitMQ by invoking the Exchange.Declare RPC
        command. When it is complete, the on_exchange_declareok method will
        be invoked by pika.

        :param str|unicode exchange_name: The name of the exchange to declare

        """
        self.logger.debug('Declaring exchange %s', exchange_name)
        self._channel.exchange_declare(exchange_name,
                                       exchange_type='topic',
                                       callback=self.on_exchange_declareok,
                                       passive=True, durable=True)

    def on_exchange_declareok(self, unused_frame):
        """Invoked by pika when RabbitMQ has finished the Exchange.Declare RPC
        command.

        :param pika.Frame.Method unused_frame: Exchange.DeclareOk response frame

        """
        self.logger.debug('Exchange declared')
        self._channel.queue_declare('', callback=self.on_queue_declareok, exclusive=True)

    def on_queue_declareok(self, method_frame):
        """Method invoked by pika when the Queue.Declare RPC call made in
        setup_queue has completed. In this method we will bind the queue
        and exchange together with the routing key by issuing the Queue.Bind
        RPC command. When this command is complete, the on_bindok method will
        be invoked by pika.

        :param pika.frame.Method method_frame: The Queue.DeclareOk frame

        """
        self.queue_name = method_frame.method.queue
        self.routing_keys_to_bind = self.routing_keys()
        self.bind_queue_to_routing_key(self.routing_keys_to_bind.pop())

    def routing_keys(self):
        return ['#']

    def bind_queue_to_routing_key(self, key):
        self.logger.info('Binding %s to %s', key, self.queue_name)
        self._channel.queue_bind(self.queue_name, 'pubsub', routing_key=key, callback=self.on_bindok)

    def add_on_cancel_callback(self):
        """Add a callback that will be invoked if RabbitMQ cancels the consumer
        for some reason. If RabbitMQ does cancel the consumer,
        on_consumer_cancelled will be invoked by pika.

        """
        self.logger.debug('Adding consumer cancellation callback')
        self._channel.add_on_cancel_callback(self.on_consumer_cancelled)

    def on_consumer_cancelled(self, method_frame):
        """Invoked by pika when RabbitMQ sends a Basic.Cancel for a consumer
        receiving messages.

        :param pika.frame.Method method_frame: The Basic.Cancel frame

        """
        self.logger.info('Consumer was cancelled remotely, shutting down: %r',
                         method_frame)
        if self._channel:
            self._channel.close()

    def on_message(self, unused_channel, basic_deliver, properties, body):
        """Invoked by pika when a message is delivered from RabbitMQ. The
        channel is passed for your convenience. The basic_deliver object that
        is passed in carries the exchange, routing key, delivery tag and
        a redelivered flag for the message. The properties passed in is an
        instance of BasicProperties with the message properties and the body
        is the message that was sent.

        :param pika.channel.Channel unused_channel: The channel object
        :param pika.Spec.Basic.Deliver: basic_deliver method
        :param pika.Spec.BasicProperties: properties
        :param str|unicode body: The message body

        """
        self.logger.info('Received message # %s: %s %s',
                         basic_deliver.delivery_tag, basic_deliver.routing_key, body)
        self.acknowledge_message(basic_deliver.delivery_tag)

    def acknowledge_message(self, delivery_tag):
        """Acknowledge the message delivery from RabbitMQ by sending a
        Basic.Ack RPC method for the delivery tag.

        :param int delivery_tag: The delivery tag from the Basic.Deliver frame

        """
        self.logger.debug('Acknowledging message %s', delivery_tag)
        self._channel.basic_ack(delivery_tag)

    def on_cancelok(self, _unused_frame, userdata):
        """This method is invoked by pika when RabbitMQ acknowledges the
        cancellation of a consumer. At this point we will close the channel.
        This will invoke the on_channel_closed method once the channel has been
        closed, which will in-turn close the connection.
        :param pika.frame.Method _unused_frame: The Basic.CancelOk frame
        :param str|unicode userdata: Extra user data (consumer tag)
        """
        self._consuming = False
        self.logger.debug(
            'RabbitMQ acknowledged the cancellation of the consumer: %s',
            userdata)
        self.close_channel()

    def close_channel(self):
        """Call to close the channel with RabbitMQ cleanly by issuing the
        Channel.Close RPC command.
        """
        self.logger.debug('Closing the channel')
        self._channel.close()

    def stop_consuming(self):
        """Tell RabbitMQ that you would like to stop consuming by sending the
        Basic.Cancel RPC command.

        """
        if self._channel:
            self.logger.debug('Sending a Basic.Cancel RPC command to RabbitMQ')
            cb = functools.partial(self.on_cancelok, userdata=self._consumer_tag)
            self._channel.basic_cancel(self._consumer_tag, cb)

    def start_consuming(self):
        """This method sets up the consumer by first calling
        add_on_cancel_callback so that the object is notified if RabbitMQ
        cancels the consumer. It then issues the Basic.Consume RPC command
        which returns the consumer tag that is used to uniquely identify the
        consumer with RabbitMQ. We keep the value to use it when we want to
        cancel consuming. The on_message method is passed in as a callback pika
        will invoke when a message is fully received.

        """
        self.logger.debug('Issuing consumer related RPC commands')
        self.add_on_cancel_callback()
        self.restart_timer()
        self._consumer_tag = self._channel.basic_consume(self.queue_name,
                                                         self.on_message,
                                                         auto_ack=False)
        self._consuming = True

    def on_bindok(self, unused_frame):
        """Invoked by pika when the Queue.Bind method has completed. At this
        point we will start consuming messages by calling start_consuming
        which will invoke the needed RPC commands to start the process.

        :param pika.frame.Method unused_frame: The Queue.BindOk response frame

        """
        self.logger.debug('Queue bound')
        if len(self.routing_keys_to_bind):
            self.bind_queue_to_routing_key(self.routing_keys_to_bind.pop())
        else:
            self.start_consuming()

    def open_channel(self):
        """Open a new channel with RabbitMQ by issuing the Channel.Open RPC
        command. When RabbitMQ responds that the channel is open, the
        on_channel_open callback will be invoked by pika.

        """
        self.logger.debug('Creating a new channel')
        self._connection.channel(on_open_callback=self.on_channel_open)

    def run(self, runtime=None):
        """Run the example consumer by connecting to RabbitMQ and then
        starting the IOLoop to block and allow the SelectConnection to operate.

        """
        if runtime:
            self._run_until = time.time() + runtime
        self._connection = self.connect()
        self._connection.ioloop.start()

    def stop(self):
        """Cleanly shutdown the connection to RabbitMQ by stopping the consumer
        with RabbitMQ. When RabbitMQ confirms the cancellation, on_cancelok
        will be invoked by pika, which will then closing the channel and
        connection. The IOLoop is started again because this method is invoked
        when CTRL-C is pressed raising a KeyboardInterrupt exception. This
        exception stops the IOLoop which needs to be running for pika to
        communicate with RabbitMQ. All of the commands issued prior to starting
        the IOLoop will be buffered but not processed.

        """
        self.logger.debug('Stopping')
        if not self._closing:
            self._closing = True
            self.logger.debug('Stopping')
            if self._consuming:
                self.stop_consuming()
                # self._connection.ioloop.start()
            else:
                self._connection.ioloop.stop()
            self.logger.debug('Stopped')

def main():
    LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) '
                  '-35s %(lineno) -5d: %(message)s')

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    amqp_prefix = 'opensuse'
    if len(sys.argv) > 1:
        amqp_prefix = sys.argv[1]
    example = PubSubConsumer(amqp_prefix, logging.getLogger(__name__))
    try:
        example.run()
    except KeyboardInterrupt:
        example.stop()


if __name__ == '__main__':
    main()
