#!/usr/bin/env python3

"""
This script parses the html files inside html/xssed/full and 
html/randomwalk/subsample as defined respectively in xssed.json and 
randomwalk.json. It outputs data.csv.
HTML files referenced on the json files that cannot be found will be discarted
(because the random sample was subsampled and some duplicated or very large
files were removed).

TODO:
- compile regex for performance
- change the structure of the code for easier reuse for prediction (?)
- count event handlers defined in JS?
""" 

import json, csv, re, esprima
from urllib.parse import unquote as urldecode
from bs4 import BeautifulSoup
from os import listdir

def import_json(filename):
    with open(filename, 'r') as f:
        return json.load(f)

def write_csv(data, filename):
    with open(filename, 'w') as f:
        reader = csv.DictWriter(f, data[0].keys())
        reader.writeheader()
        reader.writerows(data)

def node_generator(node):
    """
    Generator that takes an Esprima object (or a Esprima node) from the esprima
    module converted as a dict, and outputs all child nodes at any level of the 
    tree. It's useful to browse the entire tree.
    Subnodes are generated by browsing all the keys. It's not the most 
    optimized way to browse the tree, because some keys will never contains 
    child nodes. But it's very simple, and you're sure to not miss any subnodes. 
    """
    if node: # not empty dict or list 
        # node is a dict
        if isinstance(node, dict):
            yield node
            for key in node:
                yield from node_generator(node[key])
        # node is a list
        elif isinstance(node, list):
            for subnode in node:
                yield from node_generator(subnode)

def parse_javascript(string, domObjects, properties, methods, filename = None):
    """
    Parse a string representing JS code and return a dict containing 
    features
    """
    data = {}
    data['js_length'] = len(string)
    # init 
    for i in domObjects:
        data['js_dom_'+i] = 0
    for i in properties:
        data['js_prop_'+i] = 0
    for i in methods:
        data['js_method_'+i] = 0
    data['js_define_function'] = 0
    data['js_string_max_length'] = 0
    data['js_function_calls'] = 0 # number of function calls
    stringsList = []
    # JS parser ported from JS to Python.
    # https://github.com/Kronuz/esprima-python
    # tolerant to continue if strict JS is not respected, see:
    # http://esprima.readthedocs.io/en/4.0/syntactic-analysis.html#tolerant-mode
    # for the definition of the tree, see:
    # https://github.com/estree/estree/blob/master/es5.md
    try:
        esprimaObject = esprima.parseScript(string, options={'tolerant':True, 
            'tokens': True}).toDict()
    except (esprima.error_handler.Error, RecursionError) as e:
        print('[ERROR] Invalid JS in {0}, on code: {1}'.format(filename, string))
        print(e)
        return None
        # Sometime the JS code is broken by the xss exploit, e.g:
        # html/xssed/full/6327ecf75cb4392df52394c2c9b01e1321b0310e:
        # <a href="JavaScript: openLookup('calendar.jsp?form=stock_form&ip=startDate&d=" onmouseover=alert(document.cookie) ...

    ## Syntactic Analysis
    for node in node_generator(esprimaObject['body']):
        try:
            if node['type'] in ['FunctionDeclaration', ]: # Function Declaration
                data['js_define_function'] += 1
            elif node['type'] in ['CallExpression', 'FunctionExpression']: # function or method calls
                data['js_function_calls'] += 1
        except KeyError:
            # some node don't have a type, e.g: 
            # {'flags': '', 'pattern': '^([a-zA-Z0-9_-])+@([a-zA-Z0-9_-])+((\\.[a-zA-Z0-9_-]{2,3}){1,2})$'}
            continue
    ## Lexical Analysis
    tokens = esprimaObject['tokens']
    # We use lexical analysis to detect dom, prop, and methods instead of 
    # syntactic analysis, for simplicity because of this case:
    #    var test = alert;
    #    test();
    # Way to bypass it:
    # Set.constructor`alert\x28document.domain\x29```
    # https://www.owasp.org/index.php/XSS_Filter_Evasion_Cheat_Sheet#ECMAScript_6
    for token in tokens:
        if token['type'] == 'Identifier':
            if token['value'] in domObjects:
                data['js_dom_'+token['value']] += 1
            elif token['value'] in properties:
                data['js_prop_'+token['value']] += 1
            elif token['value'] in methods:
                data['js_method_'+token['value']] += 1
        elif token['value'] == "string":
            stringsList.append(tokens['value'])
    # max length of strings
    if len(stringsList) > 0:
        data['js_string_max_length'] = max([len(i) for i in stringsList])
    return data

def js_protocol(string):
    """
    Input a string, outputs the JS code if the string corresponds to a JS 
    pseudo-protocol or None if it's not.
    """ 
    # ignore case, white space, and new line (important)
    is_js = re.search(r'^\s*javascript:(.*)', string, 
        flags=(re.IGNORECASE|re.DOTALL))
    if bool(is_js):
        return is_js.group(1)
    else:
        return None

# def has_javascript_protocol(tag):
#     #TODO
#     for i in tag.attrs:
#         tag.attrs[i]
#     return 

def parse_html(filename,
            tags = ('script', 'iframe', 'meta', 'div', 'applet', 'object', 
            'embed', 'link', 'svg'), # tags to count
            attrs = ('href', 'http-equiv', 'lowsrc'), # attributes to count
            ):
    """
    Parses filename and returns a dict of features for future model uses
    """
    try:
        with open(filename, 'r', errors='backslashreplace') as f:
            # avoid UnicodeDecodeError, e.g with file: 
            # xssed/full/6327ecf75cb4392df52394c2c9b01e1321b0310e
            raw_html = f.read()
    except FileNotFoundError as e:
        print("File not found. Skipping file: {0}".format(filename))
        return None
    soup = BeautifulSoup(raw_html, "html5lib")
    ## Init variables
    data = {}
    # names of html attributes to define event handlers 
    # extracted from http://help.dottoro.com/larrqqck.php
    # using scrapy: response.xpath('//tr/td[2]/a/text()').extract()
    eventHandlersAttr = ['onabort', 'onactivate', 'onafterprint', 
    'onafterupdate', 'onbeforeactivate', 'onbeforecopy', 'onbeforecut',
    'onbeforedeactivate', 'onbeforeeditfocus', 'onbeforepaste', 'onbeforeprint',
    'onbeforeunload', 'onbeforeupdate', 'onblur', 'onbounce', 'oncellchange', 
    'onchange', 'onclick', 'oncontextmenu', 'oncontrolselect', 'oncopy', 
    'oncut', 'ondataavailable', 'ondatasetchanged', 'ondatasetcomplete', 
    'ondblclick', 'ondeactivate', 'ondrag', 'ondragend', 'ondragenter', 
    'ondragleave', 'ondragover', 'ondragstart', 'ondrop', 'onerror', 
    'onerrorupdate', 'onfilterchange', 'onfinish', 'onfocus', 'onfocusin', 
    'onfocusout', 'onhashchange', 'onhelp', 'oninput', 'onkeydown', 
    'onkeypress', 'onkeyup', 'onload', 'onlosecapture', 'onmessage', 
    'onmousedown', 'onmouseenter', 'onmouseleave', 'onmousemove', 'onmouseout', 
    'onmouseover', 'onmouseup', 'onmousewheel', 'onmove', 'onmoveend', 
    'onmovestart', 'onoffline', 'ononline', 'onpaste', 'onpropertychange', 
    'onreadystatechange', 'onreset', 'onresize', 'onresizeend', 'onresizestart',
    'onrowenter', 'onrowexit', 'onrowsdelete', 'onrowsinserted', 'onscroll', 
    'onsearch', 'onselect', 'onselectionchange', 'onselectstart', 'onstart', 
    'onstop', 'onsubmit', 'onunload']

    for tag in tags:
        data['html_tag_' + tag] = 0
    # HTML attrs
    for attr in attrs:
        data['html_attr_' + attr] = 0
    # Events Handlers
    for event in eventHandlersAttr:
        data['html_event_' + event] = 0
    # reference to JS file
    data['js_file'] = bool(soup.find_all('script', src=True))

    ## Extract JS code
    # JS will be extracted from <script> tag, event handlers, javascript: link
    # cf: https://stackoverflow.com/questions/12008172/how-many-ways-are-to-call-javascript-code-from-html
    javascriptStrings = []
    # 1. from <script>
    for tag in soup.find_all('script', src=False):
        javascript = tag.string
        if javascript is None:
            # check is None, ie. <script> has a child node
            print('[INFO] Skipping a ill-formed <script> in file {0}: {1}'.format(filename, tag))
        else:
            javascriptStrings.append(javascript)
    # 2. JS executed from javascript: links
    for tag in soup.find_all('a', attrs={'href':True}):
        javascript = js_protocol(tag['href'])
        if javascript:
            javascriptStrings.append(javascript)
    # 3. JS executed from javascript form
    for tag in soup.find_all('form', attrs={'action':True}):
        javascript = js_protocol(tag['action'])
        if javascript:
            javascriptStrings.append(javascript)
    # 4. JS executed from javascript iframe
    # https://www.owasp.org/index.php/XSS_Filter_Evasion_Cheat_Sheet#IFRAME
    for tag in soup.find_all('iframe', attrs={'src':True}):
        javascript = js_protocol(tag['src'])
        if javascript:
            javascriptStrings.append(javascript)
    # 5. JS executed from javascript frame
    # https://www.owasp.org/index.php/XSS_Filter_Evasion_Cheat_Sheet#FRAME
    for tag in soup.find_all('frame', attrs={'src':True}):
        javascript = js_protocol(tag['src'])
        if javascript:
            javascriptStrings.append(javascript)

    ## count tags
    for tag in tags:
        data['html_tag_' + tag] = len(soup.find_all(tag))
    ## count attributes
    for attr in attrs:
        data['html_attr_' + attr] = len(soup.find_all(attrs={attr: True}))
    ## count event handlers
    for event in eventHandlersAttr:
        event_tags = soup.find_all(attrs={event: True})
        data['html_event_' + event] = len(event_tags)
        # 6. JS executed from EventHandlers
        for event_tag in event_tags:
            javascriptStrings.append(event_tag[event])
    ## parse JS code
    domObjects = ('windows', 'location', 'document')
    properties = ('cookie', 'document', 'referrer') #location
    methods = ('write', 'getElementsByTagName', 'alert', 'eval', 'fromCharCode',
        'prompt', 'confirm')
    data_js = [] # list of the features of JS codes 
    for js in javascriptStrings:
        # parse each JS code
        data_current_js = parse_javascript(js, domObjects=domObjects, 
            properties=properties, methods=methods, filename=filename)
        if data_current_js is not None: # esprima successfully parse the code
            data_js.append(data_current_js)
    # process the features: from features at JS level to features at html
    # level
    # max dom, prop, and methods
    if data_js == []:
        # no JS in the page
        # can append if the pentester just wants to show that he can add iframe
        # tags. Ex: http://vuln.xssed.net/2012/02/27/reseller-sisko.kamadeva.com
        data_js.append(parse_javascript('', domObjects=domObjects, 
            properties=properties, methods=methods))
    for dom in domObjects:
        data['js_dom_'+dom] = max(i['js_dom_'+dom] for i in data_js)
    for prop in properties:
        data['js_prop_'+prop] = max(i['js_prop_'+prop] for i in data_js)
    for method in methods:
        data['js_method_'+method] = max(i['js_method_'+method] for i in data_js)
    # min
    data['js_min_length'] = min([i['js_length'] for i in data_js])
    data['js_min_define_function'] = min([i['js_define_function'] for i in data_js])
    data['js_min_function_calls'] = min([i['js_function_calls'] for i in data_js])
    # max
    data['js_string_max_length'] = max([i['js_string_max_length'] for i in data_js])
    
    ## other html features
    data['html_length'] = len(raw_html)
    return data

def parse_url(string):
    """
    Parses a URL as str and returns a dict of features for future model uses
    """
    string = urldecode(string)
    data = {}
    data['url_length'] = len(string)
    data['url_duplicated_characters'] = ('<<' in string) or ('>>' in string)
    data['url_special_characters'] = any(i in string for i in '"\'>') 
        # ex: ", ">, "/> 
        # idea to bypass: using `
    data['url_script_tag'] = bool(re.search(r'<\s*script.*>|<\s*/\s*script\s*>',
        string, flags=re.IGNORECASE))
        # check for whitespace and ignore case
        # checked on https://www.owasp.org/index.php/XSS_Filter_Evasion_Cheat_Sheet
    data['url_cookie'] = ('document.cookie' in string)
    data['url_redirection'] = any(i in string for i in ['window.location', 
        'window.history', 'window.navigate', 'document.URL', 
        'document.documentURI', 'document.URLUnencoded', 'document.baseURI',
        'location', 'window.open', 'self.location', 'top.location'])
        # From paper:
        # window.location, window.history, window.navigate
        # From: https://code.google.com/archive/p/domxsswiki/wikis/LocationSources.wiki
        # document.URL, document.documentURI, document.URLUnencoded,
        # document.baseURI, location, location.href, location.search, 
        # location.hash, location.pathname
        # window.open
        # https://stackoverflow.com/a/21396837
        # self.location, top.location
        # jQuery: $(location).attr('href','http://www.example.com')
        #         $(window).attr('location','http://www.example.com')
        #         $(location).prop('href', 'http://www.example.com')
        # https://stackoverflow.com/a/4745012
        # document.location
    data['url_number_keywords'] = sum(i in string for i in ['login', 'signup', 
        'contact', 'search', 'query', 'redirect', # from "Prediction of 
        #Cross-Site Scriting Attack Using Machine Learning Algoritms"
        'XSS', 'banking', 'root', 'password', 'crypt', 'shell', 'evil' ])
        # from "Automatic Classification of Cross-Site Scripting in Web Pages Using Document-based and URL-based Features"
        # TODO
    data['url_number_domain'] = len(re.findall(
        r'(?:(?!-)[A-Za-z0-9-]{1,63}(?!-)\.)+[A-Za-z]{2,6}', string))
        # adapted from: http://www.mkyong.com/regular-expressions/domain-name-regular-expression-example/
        # idea to bypass: IDN domain names: https://stackoverflow.com/a/26987741
        # becareful to decode URL before 
    return data

def printProgressBar (iteration, total, prefix = '', suffix = '', decimals = 1, length = 100, fill = '█'):
    """
    Code from: https://stackoverflow.com/a/34325723
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        length      - Optional  : character length of bar (Int)
        fill        - Optional  : bar fill character (Str)
    """
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    print('\r%s |%s| %s%% %s' % (prefix, bar, percent, suffix), end = '\r')
    # Print New Line on Complete
    if iteration == total: 
        print()

def main():
    data = []
    data_rw = import_json('randomwalk.json')
    number_pages_total = len(data_rw)
    path_randomwalk = 'html/randomsample/subsample/'
    files_randomwalk = [path_randomwalk+i for i in listdir(path_randomwalk)]
    # Initial call to print 0% progress
    printProgressBar(0, number_pages_total, prefix = 'Progress:', 
        suffix = 'Complete', length = 50)
    for i, page in enumerate(data_rw):
        feature_class = {'class': 0} # benign
        # regexp to be compatible with spider before commit b651f88
        file_path = re.sub(r'html/randomsample(/full)?/', path_randomwalk,
            page['file_path'])
        if file_path not in files_randomwalk:
            continue
        features_html = parse_html(file_path)
        if features_html is None: # file not found, do not write
            continue
        features_url = parse_url(page['url'])
        # merge dicts
        features_page = {**feature_class, **features_url, **features_html}
        data.append(features_page)
        if i % 20 == 0:
            printProgressBar(i + 1, number_pages_total, prefix = 'Progress benign:',
                suffix = 'Complete', length = 50)
    data_xssed = import_json('xssed.json')
    path_xssed = 'html/xssed/full/'
    files_xssed = ['full/'+i for i in listdir(path_xssed)]
    number_pages_total = len(data_xssed)
    for i, page in enumerate(data_xssed):
        try:
            file_path = page['files'][0]['path'] # fin the form: full/xxxx
        except IndexError:
            # no file downloaded
            # some mirrored pages are buggy, e.g 
            # http://vuln.xssed.net/2012/02/16/the-ethical-hacker.com/
            print('[INFO] skipping xss: {0}'.format(page['url']))
        if file_path not in files_xssed:
            continue
        if page['category'] not in ['XSS', 'Script Insertion']:
            print('''Warning: non-XSS vuln imported. please check if it should
            be removed: {0}'''.format(page['url']))
        feature_class = {'class': 1} # xss
        features_url = parse_url(page['url'])
        features_html = parse_html('html/xssed/'+file_path)
        if features_html is None: # file not found, do not write
            continue
        features_page = {**feature_class, **features_url, **features_html} # merge dicts
        data.append(features_page)
        if i % 20 == 0:
            printProgressBar(i + 1, number_pages_total, prefix = 'Progress malicious:',
                suffix = 'Complete', length = 50)
    write_csv(data, '../data.csv')

if __name__ == "__main__":
    main()
