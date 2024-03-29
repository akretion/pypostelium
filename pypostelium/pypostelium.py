# -*- coding: utf-8 -*-
###############################################################################
#
#   Python Point Of Sale Display Librarie
#   Copyright (C) 2014 Akretion (http://www.akretion.com).
#   @author Alexis de Lattre <alexis.delattre@akretion.com>
#   @author Sébastien BEAU <sebastien.beau@akretion.com>
#   @author Sylvain CALADOR <sylvain.calador@akretion.com>
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU Affero General Public License as
#   published by the Free Software Foundation, either version 3 of the
#   License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU Affero General Public License for more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################

import simplejson
import time
import logging
import curses.ascii
from serial import Serial
import serial.tools.list_ports

import pycountry

logger = logging.getLogger(__name__)

# Name of Payment Terminal main providers
AUTO_DEVICE_NAMES = ["ttyACM", "Desk500"]

class Driver(object):
    def __init__(self, config):
        self.device_name = config.get(
            'telium_terminal_device_name', '/dev/ttyACM0')
        self.device_rate = int(config.get(
            'telium_terminal_device_rate', 9600))
        self.retry_count = int(config.get(
            'telium_terminal_retry_count', 0))
        self.serial = False
        if self.device_name == "auto":
            self.device_name = self._find_auto_device_name()

    def _find_auto_device_name(self):
        connected_comports = [tuple(p) for p in list(serial.tools.list_ports.comports())]
        logger.debug(connected_comports)
        for port in connected_comports:
            if any(string in port[1] for string in AUTO_DEVICE_NAMES):
                return port[0]

    def serial_write(self, text):
        assert isinstance(text, str), 'text must be a string'
        self.serial.write(text.encode('ascii'))

    def initialize_msg(self):
        max_attempt = self.retry_count + 1
        attempt_nr = 0
        while attempt_nr < max_attempt:
            attempt_nr += 1
            self.send_one_byte_signal('ENQ')
            if self.get_one_byte_answer('ACK'):
                return True
            else:
                logger.warning("Terminal : SAME PLAYER TRY AGAIN")
                self.send_one_byte_signal('EOT')
                # Wait 1 sec between each attempt
                time.sleep(1)
        return False

    def send_one_byte_signal(self, signal):
        ascii_names = curses.ascii.controlnames
        assert signal in ascii_names, 'Wrong signal'
        char = ascii_names.index(signal)
        self.serial_write(chr(char))
        logger.debug('Signal %s sent to terminal' % signal)

    def get_one_byte_answer(self, expected_signal):
        ascii_names = curses.ascii.controlnames
        one_byte_read = self.serial.read(1).decode('ascii')
        expected_char = ascii_names.index(expected_signal)
        if one_byte_read == chr(expected_char):
            logger.debug("%s received from terminal" % expected_signal)
            return True
        else:
            return False

    def prepare_data_to_send(self, payment_info_dict):
        amount = payment_info_dict['amount']
        if payment_info_dict['payment_mode'] == 'check':
            payment_mode = 'C'
        elif payment_info_dict['payment_mode'] == 'card':
            payment_mode = '1'
        else:
            logger.error(
                "The payment mode '%s' is not supported"
                % payment_info_dict['payment_mode'])
            return False
        cur_decimals = payment_info_dict.get('currency_decimals', 2)
        cur_fact = 10**cur_decimals
        cur_iso_letter = payment_info_dict['currency_iso'].upper()
        if cur_iso_letter == 'EUR':  # Small speed-up
            cur_numeric = '978'
        else:
            try:
                cur = pycountry.currencies.get(alpha_3=cur_iso_letter)
                cur_numeric = str(cur.numeric)
            except Exception:
                logger.error("Currency %s is not recognized" % cur_iso_letter)
                return False
        data = {
            'pos_number': str(1).zfill(2),
            'answer_flag': '0',
            'transaction_type': '0',
            'payment_mode': payment_mode,
            'currency_numeric': cur_numeric.zfill(3),
            'private': ' ' * 10,
            'delay': 'A011',
            'auto': 'B010',
            'amount_msg': ('%.0f' % (amount * cur_fact)).zfill(8),
        }
        return data

    def generate_lrc(self, real_msg_with_etx):
        lrc = 0
        for char in real_msg_with_etx:
            lrc ^= ord(char)
        return lrc

    def send_message(self, data):
        '''We use protocol E+'''
        ascii_names = curses.ascii.controlnames
        real_msg = (
            data['pos_number']
            + data['amount_msg']
            + data['answer_flag']
            + data['payment_mode']
            + data['transaction_type']
            + data['currency_numeric']
            + data['private']
            + data['delay']
            + data['auto']
            )
        logger.debug('Real message to send = %s' % real_msg)
        assert len(real_msg) == 34, 'Wrong length for protocol E+'
        real_msg_with_etx = real_msg + chr(ascii_names.index('ETX'))
        lrc = self.generate_lrc(real_msg_with_etx)
        message = chr(ascii_names.index('STX')) + real_msg_with_etx + chr(lrc)
        self.serial_write(message)
        logger.info('Message sent to terminal')

    def compare_data_vs_answer(self, data, answer_data):
        for field in [
                'pos_number', 'amount_msg',
                'currency_numeric', 'private']:
            if data[field] != answer_data[field]:
                logger.warning(
                    "Field %s has value '%s' in data and value '%s' in answer"
                    % (field, data[field], answer_data[field]))

    def parse_terminal_answer(self, real_msg, data):
        answer_data = {
            'pos_number': real_msg[0:2],
            'transaction_result': real_msg[2],
            'amount_msg': real_msg[3:11],
            'payment_mode': real_msg[11],
            'currency_numeric': real_msg[12:15],
            'private': real_msg[15:26],
        }
        logger.debug('answer_data = %s' % answer_data)
        self.compare_data_vs_answer(data, answer_data)
        return answer_data

    def get_answer_from_terminal(self, data):
        ascii_names = curses.ascii.controlnames
        full_msg_size = 1+2+1+8+1+3+10+1+1
        msg = self.serial.read(size=full_msg_size).decode('ascii')
        logger.debug('%d bytes read from terminal' % full_msg_size)
        assert len(msg) == full_msg_size, 'Answer has a wrong size'
        if msg[0] != chr(ascii_names.index('STX')):
            logger.error(
                'The first byte of the answer from terminal should be STX')
        if msg[-2] != chr(ascii_names.index('ETX')):
            logger.error(
                'The byte before final of the answer from terminal '
                'should be ETX')
        lrc = msg[-1]
        computed_lrc = chr(self.generate_lrc(msg[1:-1]))
        if computed_lrc != lrc:
            logger.error(
                'The LRC of the answer from terminal is wrong')
        real_msg = msg[1:-2]
        logger.debug('Real answer received = %s' % real_msg)
        return self.parse_terminal_answer(real_msg, data)

    def transaction_start(self, payment_info):
        '''This function sends the data to the serial/usb port.
        '''
        payment_info_dict = simplejson.loads(payment_info)
        assert isinstance(payment_info_dict, dict), \
            'payment_info_dict should be a dict'
        logger.debug("payment_info_dict = %s" % payment_info_dict)
        res = False
        try:
            logger.debug(
                'Opening serial port %s for payment terminal with baudrate %d'
                % (self.device_name, self.device_rate))
            # IMPORTANT : don't modify timeout=3 seconds
            # This parameter is very important ; the Telium spec say
            # that we have to wait to up 3 seconds to get LRC
            self.serial = Serial(
                self.device_name, self.device_rate,
                timeout=3)
            logger.debug('serial.is_open = %s' % self.serial.isOpen())
            if self.initialize_msg():
                data = self.prepare_data_to_send(payment_info_dict)
                if not data:
                    return res
                self.send_message(data)
                if self.get_one_byte_answer('ACK'):
                    self.send_one_byte_signal('EOT')
                    res = True

                    logger.info("Now expecting answer from Terminal")
                    if self.get_one_byte_answer('ENQ'):
                        self.send_one_byte_signal('ACK')
                        self.get_answer_from_terminal(data)
                        self.send_one_byte_signal('ACK')
                        if self.get_one_byte_answer('EOT'):
                            logger.info("Answer received from Terminal")

        except Exception as e:
            logger.error('Exception in serial connection: %s' % str(e))
        finally:
            if self.serial:
                logger.debug('Closing serial port for payment terminal')
                self.serial.close()
        return res

    def get_status(self, **kwargs):
        # When I use Odoo POS v8, it regularly goes through that code
        # and sends 999.99 to the credit card reader !!!
        # Si I comment the line below -- Alexis
        # telium_driver.push_task('transaction_start', json.dumps(
        #    self.get_payment_info_from_price(999.99, 'card'), sort_keys=True))
        # TODO Improve : Get the real model connected
        status = "disconnected"
        messages = []
        connected_comports = [tuple(p) for p in list(serial.tools.list_ports.comports())]
        logger.debug(connected_comports)
        ports = [p[0] for p in connected_comports]
        if self.device_name in ports:
            status = "connected"
        else:
            devices = [p[1] for p in connected_comports]
            messages = ["Available device:"] + devices
        if status == "connected":
            self.vendor_product = "telium_image"
        else:
            self.vendor_product = False
        return {"status": status, "messages": messages}
