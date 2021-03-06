#!/usr/bin/env python3

__version__ = '1.1'
__author__ = 'Felix Friedlander <felixphew0@gmail.com>'

from string import Template
from wsgiref.simple_server import make_server
from Crypto.Cipher import Blowfish

import re
import os
import json
import cgi
import html
import sqlite3
import mysql.connector

try:
    import crypto_secrets
except ModuleNotFoundError:
    print('''It looks like you're missing crypto_secrets.py.

crypto_secrets.py contains all the secrets necessary for the various
URL obfuscation schemes that PokePaste has used. This includes the mod
and two keys used for the original obfuscation scheme, and the blowfish
key for the new scheme.

If you're setting up your own instance of PokePaste, the most important
parameter is crypto_secrets.key, which should be a bytestring of 448
bits or shorter.
''')
    exit()

cipher = Blowfish.new(crypto_secrets.key)

# We pass image requests to open() basically unmodified,
# so this regex is needed to filter out any funny business.
img_re = re.compile(r'img/(pokemon/\d+-\d+|items/\d+).png')

conn = mysql.connector.connect(user='pokepaste',
                               password=crypto_secrets.mysql_pass,
                               database='pokepaste',
                               unix_socket='/tmp/mysql.sock',
                               charset='utf8mb4',
                               collation='utf8mb4_unicode_ci',
                               autocommit=True)

pokemon_data = json.load(open('data/pokemon.json', encoding='utf-8'))
item_data = json.load(open('data/items.json', encoding='utf-8'))
move_data = json.load(open('data/moves.json', encoding='utf-8'))

html_template = {}
for html_file in ('paste', 'paste-mon'):
    fd = open('html/{}.html'.format(html_file))
    html_template[html_file] = Template(fd.read())
    fd.close()

html_static = {}
for html_file in ('404', 'create', 'howto'):
    fd = open('html/{}.html'.format(html_file))
    html_static[html_file] = fd.read()
    fd.close()

imgcss = ''
for pokemon in pokemon_data.values(): imgcss += '''
\t\t.pokemon-{id}-{no} {{
\t\t\tbackground-image: url('../img/pokemon/{id}-{no}.png');
\t\t}}
'''.format(id=pokemon['id'], no=pokemon['no'])
for item in item_data.values(): imgcss += '''
\t\t.item-{id} {{
\t\t\tbackground-image: url('../img/items/{id}.png');
\t\t}}
'''.format(id=item['id'])
html_template['paste'] = Template(
        html_template['paste'].safe_substitute(imgcss=imgcss))

def encrypt_id_v1(id):
    return (id * crypto_secrets.key1) % crypto_secrets.mod

def decrypt_id_v1(id):
    return (id * crypto_secrets.key2) % crypto_secrets.mod

def encrypt_id_v2(id):
    return cipher.encrypt(id.to_bytes(8, 'big')).hex()

def decrypt_id_v2(id):
    return int.from_bytes(cipher.decrypt(bytes.fromhex(id)), 'big')

def format_paste(paste, title, author, notes):

    paste = html.escape(paste)

    html_mons = ''

    for mon in paste.split('\r\n\r\n'):

        if mon.strip() == '': continue

        mon_lines = mon.splitlines()
        line = mon_lines.pop(0)

        pokemonid = '0-0'
        itemid = 0

        try:
            index = len(line)

            if '(M)' in line:
                index = line.rindex('(M)')
                lindex = index + 1
                rindex = index + 2
                if '(' not in line[rindex + 1:]:
                    line = (line[:lindex]
                          + '<span class="gender-m">'
                          + line[lindex:rindex]
                          + '</span>'
                          + line[rindex:])

            if '(F)' in line:
                index = line.rindex('(F)')
                lindex = index + 1
                rindex = index + 2
                if '(' not in line[rindex + 1:]:
                    line = (line[:lindex]
                          + '<span class="gender-f">'
                          + line[lindex:rindex]
                          + '</span>'
                          + line[rindex:])

            if ')' in line[:index] and '(' in line[line[:index].rindex('('):]:
                rindex = line[:index].rindex(')')
                lindex = line[:rindex].rindex('(') + 1
                if line[lindex:rindex] in pokemon_data:
                    pokemon = pokemon_data[line[lindex:rindex]]
                    pokemonid = '{}-{}'.format(pokemon['id'], pokemon['no'])
                    line = (line[:lindex]
                          + '<span class="type-{}">'.format(pokemon['type'])
                          + line[lindex:rindex]
                          + '</span>'
                          + line[rindex:])
            else:
                lineparts = line[:index].partition('@')
                if lineparts[0].strip() in pokemon_data:
                    pokemon = pokemon_data[lineparts[0].strip()]
                    pokemonid = '{}-{}'.format(pokemon['id'], pokemon['no'])
                    line = ('<span class="type-{}">'.format(pokemon['type'])
                          + lineparts[0]
                          + '</span>'
                          + lineparts[1]
                          + lineparts[2]
                          + line[index:])

            lineparts = line.rpartition('@')
            if lineparts[2].strip() in item_data:
                item = item_data[lineparts[2].strip()]
                itemid = item['id']
                try:
                    line = (lineparts[0]
                          + lineparts[1]
                          + '<span class="type-{}">'.format(item['type'])
                          + lineparts[2]
                          + '</span>')
                except KeyError:
                    pass

        finally:
            mon_formatted = line + '\n'

        for line in mon_lines:
            try:
                if line[0] == '-' and line[1:].strip() in move_data:
                    move_type = move_data[line[1:].strip()]['type']
                    mon_formatted += '<span class="type-{}">'.format(move_type)
                    mon_formatted += line[0]
                    mon_formatted += '</span>'
                    mon_formatted += line[1:]
                elif ':' in line:
                    index = line.index(':') + 1
                    mon_formatted += '<span class="heading">'
                    mon_formatted += line[:index]
                    mon_formatted += '</span>'
                    if line[:5] in ('EVs: ', 'IVs: '):
                        mon_formatted += ' '
                        statsline = line[5:].rstrip().split(' / ')
                        for i, stat in enumerate(statsline):
                            if stat.endswith('HP'): s = 'stat-hp'
                            elif stat.endswith('Atk'): s = 'stat-atk'
                            elif stat.endswith('Def'): s = 'stat-def'
                            elif stat.endswith('SpA'): s = 'stat-spa'
                            elif stat.endswith('SpD'): s = 'stat-spd'
                            elif stat.endswith('Spe'): s = 'stat-spe'
                            else: s = ''
                            mon_formatted += '<span class="stat-wrap {}">'.format(s)
                            mon_formatted += stat
                            mon_formatted += '</span>'
                            if i != len(statsline) - 1: mon_formatted += ' / '
                    else:
                        mon_formatted += line[index:]
                else:
                    mon_formatted += line
            except:
                mon_formatted += line
            finally:
                mon_formatted += '\n'

        mon_formatted += '\n'

        html_mons += html_template['paste-mon'].substitute(pokemonid=pokemonid,
                                                           itemid=itemid,
                                                           paste=mon_formatted)

    if title:
        title = html.escape(title)
    else:
        title = 'Untitled'

    if author:
        author = html.escape(author)
    else:
        author = 'Anonymous'

    if notes:
        notes = html.escape(notes).replace('\r\n', '</p><p>')
    else:
        notes = ''

    return html_template['paste'].substitute(mons=html_mons,
                                             title=title,
                                             author=author,
                                             notes=notes)

def retrieve_paste(id, start_response):
    c = conn.cursor()
    c.execute('SELECT paste, title, author, notes FROM pastes WHERE id = %s',
              (id,))
    paste = c.fetchone()
    if paste:
        response = format_paste(*paste).encode('utf-8')
        status = '200 OK'
        headers = [
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Content-Length', str(len(response)))
        ]
        start_response(status, headers)
        return [response]
    else:
        return generic_404(start_response)

def generic_404(start_response, status='404 Not Found'):
    response = html_static['404'].encode('utf-8')
    headers = [
        ('Content-Type', 'text/html; charset=utf-8'),
        ('Content-Length', str(len(response)))
    ]
    start_response(status, headers)
    return [response]

def application(environ, start_response):
    method = environ['REQUEST_METHOD']
    path = environ['PATH_INFO'].strip('/')

    if method == 'GET':

        if len(path) == 16:
            # Requesting a paste - new encrypted ID
            try:
                id = decrypt_id_v2(path)
            except:
                return generic_404(start_response)
            else:
                return retrieve_paste(id, start_response)

        elif path.isdigit():
            # Requesting a paste - unencrypted or old encrypted ID?
            id = int(path)
            if id >= 256:
                # Old encrypted ID
                id = decrypt_id_v1(id)
                if id >= 1000:
                    # Prevent using old hash format on new pastes
                    return generic_404(start_response)
            return retrieve_paste(id, start_response)


        elif not path:
            # Requesting /, return the (static) submit page
            response = html_static['create'].encode('utf-8')
            status = '200 OK'
            headers = [
                ('Content-Type', 'text/html; charset=utf-8'),
                ('Content-Length', str(len(html_static['create'])))
            ]
            start_response(status, headers)
            return [response]

        elif path == 'howto':
            # Requesting the (static) PokePaste HOWTO
            response = html_static['howto'].encode('utf-8')
            status = '200 OK'
            headers = [
                ('Content-Type', 'text/html; charset=utf-8'),
                ('Content-Length', str(len(html_static['howto'])))
            ]
            start_response(status, headers)
            return [response]


        elif img_re.fullmatch(path):
            # Requesting an image
            try:
                rfile = open(path, mode='rb')
                fsize = os.stat(path).st_size
                bsize = os.statvfs(path).f_bsize
            except:
                return generic_404(start_response)
            else:
                status = '200 OK'
                headers = [
                    ('Content-Type', 'image/png'),
                    ('Content-Length', str(fsize))
                ]
                start_response(status, headers)
                if 'wsgi.file_wrapper' in environ:
                    return environ['wsgi.file_wrapper'](rfile, bsize)
                else:
                    return iter(lambda: rfile.read(bsize), b'')

        else:
            return generic_404(start_response)

    elif method == 'POST':

        if path == 'create':
            # Submit a new paste
            form = cgi.FieldStorage(fp=environ['wsgi.input'], environ=environ)
            if form.getvalue('paste'):
                c = conn.cursor()
                c.execute('INSERT INTO pastes (paste, title, author, notes) VALUES (%s, %s, %s, %s)',
                          [form.getvalue(key) for key in ('paste', 'title', 'author', 'notes')])
                status = '302 Found'
                headers = [
                    ('Location', '/{}'.format(encrypt_id_v2(c.lastrowid)))
                ]
                start_response(status, headers)
                return [b'']
            else:
                return generic_404(start_response, '400 Bad Request')
            response = form['paste'].value.encode('utf-8')
            status = '200 OK'
            headers = [
                ('Content-Type', 'text/plain; charset=utf-8'),
                ('Content-Length', str(len(form['paste'].value)))
            ]
            start_response(status, headers)
            return [response]
        else:
            return generic_404(start_response, '405 Method Not Allowed')

if __name__ == '__main__':
    with make_server('', 8000, application) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            conn.close()
            quit()
