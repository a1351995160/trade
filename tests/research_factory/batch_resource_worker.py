"""资源边界测试载荷；只在握手完成后分配内存或阻塞。"""
from chanlun_trader.synthetic_batch_resources import worker_resource_handshake

worker_resource_handshake()

import sys
import time

if sys.argv[1] == "memory":
    blocks = []
    while True:
        blocks.append(bytearray(8 * 1024 * 1024))
elif sys.argv[1] == "timeout":
    time.sleep(10)
else:
    print("BOUNDED_WORKER_COMPLETED", flush=True)
