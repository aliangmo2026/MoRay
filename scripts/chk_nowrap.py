# -*- coding: utf-8 -*-
import io

txt = io.open(r'D:\ai工具台\web\index.html', encoding='utf-8').read()
css = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis'
print('nowrap css present:', css in txt)
