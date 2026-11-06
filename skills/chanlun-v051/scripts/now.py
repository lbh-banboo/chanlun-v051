# -*- coding: utf-8 -*-
"""打印本机真实时间与盘口状态。定时任务落盘文件名（STAMP）、判断"今天是不是交易日/盘中还是收盘后"一律用它，
不要用沙箱/容器里的时钟（容器时区与时钟可能不对）。"""
import datetime
n = datetime.datetime.now()
print(n.strftime('%Y-%m-%d %H:%M:%S %A'))
h, m = n.hour, n.minute
if (h, m) < (9, 30):
    s = '开盘前'
elif (h, m) <= (11, 30):
    s = '上午盘中'
elif (h, m) < (13, 0):
    s = '午间休市'
elif (h, m) < (15, 0):
    s = '下午盘中'
else:
    s = '已收盘'
print('盘口状态: %s' % s)
