#! /usr/bin/python3

# https://www.djangosnippets.org/snippets/96/
# Considered public domain

from datetime import datetime
import asyncore
from smtpd import SMTPServer


class EmlServer(SMTPServer):
    no = 0

    def process_message(self, peer, mailfrom, rcpttos, data):
        filename = '%s-%d.eml' % (datetime.now().strftime('%Y%m%d%H%M%S'),
                                  self.no)
        f = open(filename, 'w')
        f.write(data)
        f.close
        print(f'{filename} saved.')
        self.no += 1


def run():
    EmlServer(('0.0.0.0', 25), None)
    try:
        asyncore.loop()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    run()
