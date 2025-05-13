# SPDX-FileCopyrightText: 2018 Carter Nelson for Adafruit Industries
#
# SPDX-License-Identifier: MIT

"""
`adafruit_bmp3xx`
====================================================

CircuitPython driver from BMP388 Temperature and Barometric Pressure sensor.

* Author(s): Carter Nelson

Implementation Notes
--------------------

**Hardware:**

* `Adafruit BMP388 - Precision Barometric Pressure and Altimeter
  <https://www.adafruit.com/product/3966>`_ (Product ID: 3966)

**Software and Dependencies:**

* Adafruit CircuitPython firmware for the supported boards:
  https://circuitpython.org/downloads

* Adafruit's Bus Device library:
  https://github.com/adafruit/Adafruit_CircuitPython_BusDevice

"""

import struct
import time

from micropython import const

try:
    from typing import Tuple

    from busio import I2C, SPI
    from digitalio import DigitalInOut
except ImportError:
    pass

__version__ = "0.0.0+auto.0"
__repo__ = "https://github.com/adafruit/Adafruit_CircuitPython_BMP3XX.git"

_BMP388_CHIP_ID = const(0x50)
_BMP390_CHIP_ID = const(0x60)

_REGISTER_CHIPID = const(0x00)
_REGISTER_STATUS = const(0x03)
_REGISTER_PRESSUREDATA = const(0x04)
_REGISTER_TEMPDATA = const(0x07)
_REGISTER_CONTROL = const(0x1B)
_REGISTER_OSR = const(0x1C)
_REGISTER_ODR = const(0x1D)
_REGISTER_CONFIG = const(0x1F)
_REGISTER_CAL_DATA = const(0x31)
_REGISTER_CMD = const(0x7E)

_OSR_SETTINGS = (1, 2, 4, 8, 16, 32)  # pressure and temperature oversampling settings
_IIR_SETTINGS = (0, 2, 4, 8, 16, 32, 64, 128)  # IIR filter coefficients


class BMP3XX:
    """Base class for BMP3XX sensor."""

    def __init__(self) -> None:
        chip_id = self._read_byte(_REGISTER_CHIPID)
        if chip_id not in {_BMP388_CHIP_ID, _BMP390_CHIP_ID}:
            raise RuntimeError(f"Failed to find BMP3XX! Chip ID {hex(chip_id)}")
        self._read_coefficients()
        self.reset()
        self.sea_level_pressure = 1013.25
        self._wait_time = 0.002  # change this value to have faster reads if needed
        """Sea level pressure in hPa."""

    @property
    def pressure(self) -> float:
        """The pressure in hPa."""
        return self._read()[0] / 100

    @property
    def temperature(self) -> float:
        """The temperature in degrees Celsius"""
        return self._read()[1]

    @property
    def altitude(self) -> float:
        """The altitude in meters based on the currently set sea level pressure."""
        # see https://www.weather.gov/media/epz/wxcalc/pressureAltitude.pdf
        return 44307.7 * (1 - (self.pressure / self.sea_level_pressure) ** 0.190284)

    @property
    def pressure_oversampling(self) -> int:
        """The pressure oversampling setting."""
        return _OSR_SETTINGS[self._read_byte(_REGISTER_OSR) & 0x07]

    @pressure_oversampling.setter
    def pressure_oversampling(self, oversample: int) -> None:
        if oversample not in _OSR_SETTINGS:
            raise ValueError(f"Oversampling must be one of: {_OSR_SETTINGS}")
        new_setting = self._read_byte(_REGISTER_OSR) & 0xF8 | _OSR_SETTINGS.index(oversample)
        self._write_register_byte(_REGISTER_OSR, new_setting)

    @property
    def temperature_oversampling(self) -> int:
        """The temperature oversampling setting."""
        return _OSR_SETTINGS[self._read_byte(_REGISTER_OSR) >> 3 & 0x07]

    @temperature_oversampling.setter
    def temperature_oversampling(self, oversample: int) -> None:
        if oversample not in _OSR_SETTINGS:
            raise ValueError(f"Oversampling must be one of: {_OSR_SETTINGS}")
        new_setting = self._read_byte(_REGISTER_OSR) & 0xC7 | _OSR_SETTINGS.index(oversample) << 3
        self._write_register_byte(_REGISTER_OSR, new_setting)

    @property
    def filter_coefficient(self) -> int:
        """The IIR filter coefficient."""
        return _IIR_SETTINGS[self._read_byte(_REGISTER_CONFIG) >> 1 & 0x07]

    @filter_coefficient.setter
    def filter_coefficient(self, coef: int) -> None:
        if coef not in _IIR_SETTINGS:
            raise ValueError(f"Filter coefficient must be one of: {_IIR_SETTINGS}")
        self._write_register_byte(_REGISTER_CONFIG, _IIR_SETTINGS.index(coef) << 1)

    def reset(self) -> None:
        """Perform a power on reset. All user configuration settings are overwritten
        with their default state.
        """
        self._write_register_byte(_REGISTER_CMD, 0xB6)

    def _read(self) -> Tuple[float, float]:  # noqa: PLR0914
        """Returns a tuple for temperature and pressure."""

        # Perform one measurement in forced mode
        self._write_register_byte(_REGISTER_CONTROL, 0x13)

        # Wait for *both* conversions to complete
        while self._read_byte(_REGISTER_STATUS) & 0x60 != 0x60:
            time.sleep(self._wait_time)

        # Get ADC values
        data = self._read_register(_REGISTER_PRESSUREDATA, 6)
        adc_p = data[2] << 16 | data[1] << 8 | data[0]
        adc_t = data[5] << 16 | data[4] << 8 | data[3]

        # datasheet, sec 9.2 Temperature compensation
        T1, T2, T3 = self._temp_calib

        pd1 = adc_t - T1
        pd2 = pd1 * T2

        temperature = pd2 + (pd1 * pd1) * T3

        # datasheet, sec 9.3 Pressure compensation
        P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11 = self._pressure_calib

        pd1 = P6 * temperature
        pd2 = P7 * temperature**2.0
        pd3 = P8 * temperature**3.0
        po1 = P5 + pd1 + pd2 + pd3

        pd1 = P2 * temperature
        pd2 = P3 * temperature**2.0
        pd3 = P4 * temperature**3.0
        po2 = adc_p * (P1 + pd1 + pd2 + pd3)

        pd1 = adc_p**2.0
        pd2 = P9 + P10 * temperature
        pd3 = pd1 * pd2
        pd4 = pd3 + P11 * adc_p**3.0

        pressure = po1 + po2 + pd4

        # pressure in hPa, temperature in deg C
        return pressure, temperature

    def _read_coefficients(self) -> None:
        """Read & save the calibration coefficients"""
        coeff = self._read_register(_REGISTER_CAL_DATA, 21)
        # See datasheet, pg. 27, table 22
        coeff = struct.unpack("<HHbhhbbHHbbhbb", coeff)
        # See datasheet, sec 9.1
        # Note: forcing float math to prevent issues with boards that
        #       do not support long ints for 2**<large int>
        self._temp_calib = (
            coeff[0] / 2**-8.0,  # T1
            coeff[1] / 2**30.0,  # T2
            coeff[2] / 2**48.0,
        )  # T3
        self._pressure_calib = (
            (coeff[3] - 2**14.0) / 2**20.0,  # P1
            (coeff[4] - 2**14.0) / 2**29.0,  # P2
            coeff[5] / 2**32.0,  # P3
            coeff[6] / 2**37.0,  # P4
            coeff[7] / 2**-3.0,  # P5
            coeff[8] / 2**6.0,  # P6
            coeff[9] / 2**8.0,  # P7
            coeff[10] / 2**15.0,  # P8
            coeff[11] / 2**48.0,  # P9
            coeff[12] / 2**48.0,  # P10
            coeff[13] / 2**65.0,
        )  # P11

    def _read_byte(self, register: int) -> int:
        """Read a byte register value and return it"""
        return self._read_register(register, 1)[0]

    def _read_register(self, register: int, length: int) -> bytearray:
        """Low level register reading, not implemented in base class"""
        raise NotImplementedError()

    def _write_register_byte(self, register: int, value: int) -> None:
        """Low level register writing, not implemented in base class"""
        raise NotImplementedError()


class BMP3XX_I2C(BMP3XX):
    """Driver for I2C connected BMP3XX.

    :param ~busio.I2C i2c: The I2C bus the BMP388 is connected to.
    :param int address: I2C device address. Defaults to :const:`0x77`.
                        but another address can be passed in as an argument

    **Quickstart: Importing and using the BMP388**

        Here is an example of using the :class:`BMP3XX_I2C` class.
        First you will need to import the libraries to use the sensor

        .. code-block:: python

            import board
            import adafruit_bmp3xx

        Once this is done you can define your `board.I2C` object and define your sensor object

        .. code-block:: python

            i2c = board.I2C()   # uses board.SCL and board.SDA
            bmp = adafruit_bmp3xx.BMP3XX_I2C(i2c)


        Now you have access to the :attr:`temperature` and :attr:`pressure` attributes

        .. code-block:: python

            temperature = bmp.temperature
            pressure = bmp.pressure

    """

    def __init__(self, i2c: I2C, address: int = 0x77) -> None:
        from adafruit_bus_device import i2c_device  # noqa: PLC0415

        self._i2c = i2c_device.I2CDevice(i2c, address)
        super().__init__()

    def _read_register(self, register: int, length: int) -> bytearray:
        """Low level register reading over I2C, returns a list of values"""
        result = bytearray(length)
        with self._i2c as i2c:
            i2c.write(bytes([register & 0xFF]))
            i2c.readinto(result)
            return result

    def _write_register_byte(self, register: int, value: int) -> None:
        """Low level register writing over I2C, writes one 8-bit value"""
        with self._i2c as i2c:
            i2c.write(bytes((register & 0xFF, value & 0xFF)))


class BMP3XX_SPI(BMP3XX):
    """Driver for SPI connected BMP3XX.

    :param ~busio.SPI spi: SPI device
    :param ~digitalio.DigitalInOut cs: Chip Select


    **Quickstart: Importing and using the BMP388**

        Here is an example of using the :class:`BMP3XX_SPI` class.
        First you will need to import the libraries to use the sensor

        .. code-block:: python

            import board
            import adafruit_bmp3xx
            from digitalio import DigitalInOut, Direction

        Once this is done you can define your `board.SPI` object and define your sensor object

        .. code-block:: python

            spi = board.SPI()
            cs = DigitalInOut(board.D5)
            bmp = adafruit_bmp3xx.BMP3XX_SPI(spi, cs)


        Now you have access to the :attr:`temperature` and :attr:`pressure` attributes

        .. code-block:: python

            temperature = bmp.temperature
            pressure = bmp.pressure

    """

    def __init__(self, spi: SPI, cs: DigitalInOut) -> None:
        from adafruit_bus_device import spi_device  # noqa: PLC0415

        self._spi = spi_device.SPIDevice(spi, cs)
        # toggle CS low/high to put BMP3XX in SPI mode
        with self._spi:
            time.sleep(0.001)
        super().__init__()

    def _read_register(self, register: int, length: int) -> bytearray:
        """Low level register reading over SPI, returns a list of values"""
        result = bytearray(length)
        with self._spi as spi:
            spi.write(bytes((0x80 | register, 0x00)))
            spi.readinto(result)
        return result

    def _write_register_byte(self, register: int, value: int) -> None:
        """Low level register writing over SPI, writes one 8-bit value"""
        with self._spi as spi:
            spi.write(bytes((register & 0x7F, value & 0xFF)))
