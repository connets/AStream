import os
import sys
from html import escape
import urllib.parse

from io import BytesIO, StringIO

def list_directory(path):
    """Helper to produce a directory listing (absent index.html).
    Return value is either a file object, or None (indicating an
    error).  In either case, the headers are sent, making the
    interface the same as for send_head().
    From SimpleHTTPServer.py

    """
    print("Get DIR listing for ", path)
    try:
        dir_list = os.listdir(path)
        print("The path for ", path, " is ", dir_list)
    except os.error:
        return BytesIO(b"404 No permission to list directory")
    dir_list.sort(key=lambda a: a.lower())
    f = StringIO()
    encoding = sys.getfilesystemencoding()
    displaypath = escape(urllib.parse.unquote(path))
    f.write('<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">')
    f.write("<html>\n<title>Directory listing for %s</title>\n" % displaypath)
    f.write("<body>\n<h2>Directory listing for %s</h2>\n" % displaypath)
    f.write("<hr>\n<ul>\n")
    for name in dir_list:
        fullname = os.path.join(path, name)
        displayname = linkname = name
        # Append / for directories or @ for symbolic links
        if os.path.isdir(fullname):
            displayname = name + "/"
            linkname = name + "/"
        if os.path.islink(fullname):
            displayname = name + "@"
            # Note: a link to a directory displays with @ and links with /
        f.write('<li><a href="%s">%s</a>\n'
                % (urllib.parse.quote(linkname), escape(displayname)))
    f.write("</ul>\n<hr>\n</body>\n</html>\n")
    html = f.getvalue().encode(encoding, "surrogateescape")
    f = BytesIO(html)
    length = len(html)
    f.seek(0)
    response = "200"
    #self.send_header("Content-type", "text/html; charset=%s" % encoding)
    #self.send_header("Content-Length", str(length))
    #self.end_headers()
    return f
