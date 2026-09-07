# -*- coding: utf-8 -*-
import io

html = io.open('web/index.html', encoding='utf-8').read()
body = html.find('<body')
banner = html.find('demoModeBanner')
print('body@%d banner@%d after-body:%s' % (body, banner, banner > body and banner < body + 700))
seg = html[banner - 30:banner + 260].replace('\n', ' ')
print(seg[:280])
