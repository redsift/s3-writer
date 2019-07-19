#!/usr/bin/env python3

import logging
import sys 

__version__ = "v0.0.1"
module = sys.modules['__main__'].__file__
log = logging.getLogger(module)

from string import Template 

import asyncio
import aiohttp
import aiofiles
import aioboto3
import socket

import time
import uuid
import datetime
import os
from pathlib import Path

HOST = socket.gethostname()

def params(index):
    now = datetime.datetime.utcnow()
    return { 
        'year': str(now.year), 
        'month': f'{now.month:02}', 
        'day': f'{now.day:02}', 
        'hour': f'{now.hour:02}', 
        'minute': f'{now.minute:02}', 
        'second': f'{now.second:02}', 
        'uuid': str(uuid.uuid4()),
        'index': str(index),
        'host': HOST 
        }

from abc import ABC, abstractmethod

class Sink(ABC):
    def __init__(self, path, template, buffer):
        self.path = path
        self.template = template
        self.buffer = buffer
        self.filename = None
        self.count = 0
        self.index = 0

        super().__init__()

    @abstractmethod
    async def new_file(self):
        self.index = self.index + 1
        self.filename = self.template.substitute(**params(self.index))
    
    @abstractmethod
    async def close_file(self):
        self.filename = None
        self.count = 0

    @abstractmethod
    async def write_bytes(self, chunk):
        pass

    async def write(self, chunk):
        if not chunk:
            await self.close_file()
            return
        
        if self.filename == None:
            await self.new_file()

        self.count = self.count + len(chunk)    
        if self.count > self.buffer:
            # scan for seperator, if found write up to in current file then close and open
            out = chunk.split(b'\n', 2)
            if len(out) == 2:
                await self.write_bytes(out[0])       
                await self.close_file()
                await self.new_file()
                chunk = out[1]
                self.count = len(chunk)

        await self.write_bytes(chunk)

class FileSink(Sink):
    def __init__(self, path, template, buffer):
        Sink.__init__(self, path, template, buffer)
        self.target = None  
        self.tmp = None
        self.fp = None

    async def new_file(self):
        await super().new_file()

        self.target = Path(self.path, self.filename)
        self.tmp = self.target.with_suffix('._')
        log.info("Writing new file %s", self.tmp)
        self.fp = await aiofiles.open(self.tmp, mode='wb')

    async def close_file(self):
        await self.fp.flush()
        await self.fp.close()

        os.rename(self.tmp, self.target)

        await super().close_file()

        self.fp = None
        self.tmp = None
        self.target = None

    async def write_bytes(self, chunk):
        await self.fp.write(chunk)

from io import BytesIO
import gzip

class S3FileSink(Sink):
    def __init__(self, path, template, buffer):
        Sink.__init__(self, path, template, buffer)
        self.client = aioboto3.client('s3')
        self.bytes = None

    async def new_file(self):
        await super().new_file()
        self.bytes = BytesIO()

    async def close_file(self):
        log.info("Writing new object %s to bucket", self.filename)
        self.bytes.seek(0)

        key = self.filename
        byte = self.bytes

        # Do Gzip
        key = key + '.gz'
        byte = BytesIO(gzip.compress(byte.getvalue()))
        byte.seek(0)

        await self.client.upload_fileobj(byte, self.path, key)

        await super().close_file()
        self.bytes = None


    async def write_bytes(self, chunk):
        self.bytes.write(chunk)

async def loop(arguments):
    s = Template(arguments.template) 
    sink = None
    
    while True:

        if arguments.s3 != None:
            sink = S3FileSink(arguments.s3, s, arguments.buffer)
        else:    
            sink = FileSink(arguments.path, s, arguments.buffer)

        auth = None
        if arguments.user != None:
            auth = aiohttp.BasicAuth(arguments.user, arguments.password)
        
        timeout = aiohttp.ClientTimeout(sock_connect=30)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(arguments.url, timeout=timeout, auth=auth, raise_for_status=True) as resp:
                    t1 = time.perf_counter()
                    count = 0
                    async for chunk in resp.content.iter_any():
                        await sink.write(chunk)

                        if not chunk:
                            break
                        count = count + len(chunk)
                        t2 = time.perf_counter()
                        if (t2 - t1) > 3.0:
                            mbs = count / ((t2 - t1) * 1024 * 1024)  
                            log.info("Downloading at %0.2f MB/s", mbs)  
                            t1 = t2
                            count = 0
            return                
        except aiohttp.client_exceptions.ClientPayloadError:
            log.exception("Exception streaming data, resetting connection")


import argparse

def parse_command_line(argv):
    formatter_class = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(description='Run the S3 writer.',
                                     formatter_class=formatter_class)
    parser.add_argument("--version", action="version",
                        version="%(prog)s {}".format(__version__))
    parser.add_argument("-v", "--verbose", dest="verbose_count",
                        action="count", default=0,
                        help="increases log verbosity for each occurence.")

    parser.add_argument('--buffer', action='store', help='Min number of bytes to store in file', default=20*1024*1024, type=int)
    parser.add_argument('--user', action='store', help='Basic auth user')
    parser.add_argument('--password', action='store', help='Basic auth password')
    parser.add_argument('--template', action='store', help='Template to expand for the filename', default='$uuid')

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--path', action='store', help='Store stream in files at path')
    group.add_argument('--s3', action='store', help='Store stream in a S3 bucket')

    parser.add_argument('url', help='URL to stream') 

    arguments = parser.parse_args(argv[1:])

    return arguments

def main():
    try:
        arguments = parse_command_line(sys.argv)
        
        # Sets log level to WARN going more verbose for each new -v.
        logging.basicConfig(stream=sys.stderr, level=int(max(logging.WARN / 10 - arguments.verbose_count, 0) * 10),
                            format='%(asctime)-15s %(name)s (%(levelname)s): %(message)s')
        
        # Do something with arguments.
        asyncio.run(loop(arguments))
    except KeyboardInterrupt:
        log.error('Program interrupted!')
    finally:
        logging.shutdown()

if __name__ == "__main__":
    sys.exit(main())