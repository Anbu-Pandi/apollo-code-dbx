import io
import logging
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP

import itertools
import pandas as pd
import numpy as np
from pretty_html_table import build_table

from common.utils.dfutil import tolist


def send_mail(fromemail, toemail, subject, body):
    message = MIMEMultipart()
    message['Subject'] = subject
    message['From'] = fromemail
    message['To'] = (', ').join(tolist(toemail))

    body_content = body
    message.attach(MIMEText(body_content, "html"))
    msg_body = message.as_string()

    server = SMTP('mail.apollolp.com')
    server.sendmail(message['From'], tolist(toemail), msg_body)
    server.quit()


def _combinehtml(components):
    return "\n".join([f"<div>{x}</div>" for x in components])


def _htmlify_txt(txt=None):
    if txt is None:
        return ""
    return f"<h3>{str(txt)}</h3>"


def _htmlify_df(df, title=None, color='blue_light', **htmlargs):
    htmltable = build_table(df, color, **htmlargs)
    htmltitle = _htmlify_txt(title)
    htmlbody = _combinehtml([htmltitle, htmltable])
    return htmlbody


def emaildf(df, fromemail, toemail, subject, attachment_title=None, color='blue_light', **htmlargs):
    logging.info(f"emailing df shape: {df.shape}")
    htmlbody = _htmlify_df(df, attachment_title, color=color, **htmlargs)
    send_mail(fromemail, toemail, subject, htmlbody)


def emaildfs(dfs, fromemail, toemail, subject, attachment_titles=None, color='blue_light', **htmlargs):
    logging.info(f"emailing dfs shape: {len(dfs)}")
    if isinstance(attachment_titles, list):
        htmlbodies = []
        for df, dftitle in zip(dfs, attachment_titles):
            htmlbodies += [_htmlify_df(df, dftitle, color=color, **htmlargs)]
        send_mail(fromemail, toemail, subject, _combinehtml(htmlbodies))
    else:
        htmlbodies = [_htmlify_txt(attachment_titles)]
        for df in dfs:
            htmlbodies += [_htmlify_df(df, color=color, **htmlargs)]
        send_mail(fromemail, toemail, subject, _combinehtml(htmlbodies))


def export_excel(df):
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer) as writer:
            df.to_excel(writer)
        return buffer.getvalue()


def export_csv(df):
    with io.StringIO() as buffer:
        df.to_csv(buffer)
        return buffer.getvalue()


def export_plt(plt):
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    return buf.getvalue()


def export_img(png):
    with open(png, 'rb') as img:
        return img.read()


EXPORT_FUNCS = {
    'xlsx': (export_excel, MIMEApplication),
    'csv': (export_csv, MIMEApplication),
    'plt': (export_plt, MIMEImage),
    'png': (export_img, MIMEImage),
    'pdf': (export_img, MIMEApplication),
    'html': None
}


def email_attachment(objects, fromemail, toemail, subject, body, filenames='df', filetypes='xlsx',
                     attachment_titles=None,
                     color='blue_light', **htmlargs):
    attachment_titles = attachment_titles or ['']
    assert np.all([filetype in EXPORT_FUNCS for filetype in tolist(filetypes)]), \
        f"incorrect filetype: {str(filetypes)} allowed types: {str(EXPORT_FUNCS.keys())}"

    multipart = MIMEMultipart()
    multipart['From'] = fromemail
    multipart['To'] = (', ').join(tolist(toemail))
    multipart['Subject'] = subject
    bodypart = [body]
    for i, (obj, filename, filetype, des) in enumerate(zip(tolist(objects),
                                                           itertools.cycle(tolist(filenames)),
                                                           itertools.cycle(tolist(filetypes)),
                                                           itertools.cycle(tolist(attachment_titles))
                                                           )):
        export_func = EXPORT_FUNCS[filetype]
        if export_func is None:
            bodypart.append(_htmlify_df(obj, des, color=color, **htmlargs))
        else:
            attachment = export_func[1](export_func[0](obj))
            if filetype in ('plt', 'png'):
                bodypart.append(_htmlify_txt(des))
                bodypart.append(f'<img src="cid:image{i}" alt="Smiley" title="{des}">')
                attachment.add_header('Content-ID', f'<image{i}>')
                attachment['Content-Disposition'] = 'inline; filename="{}.{}"'.format(filename, filetype)
                multipart.attach(attachment)
            else:
                attachment['Content-Disposition'] = 'attachment; filename="{}.{}"'.format(filename, filetype)
            multipart.attach(attachment)

    multipart.attach(MIMEText(_combinehtml(bodypart), 'html'))
    server = SMTP('mail.apollolp.com')
    server.sendmail(fromemail, toemail, multipart.as_string())
    server.quit()


if __name__ == '__main__':
    fromemail = 'agite@apollo.com'
    toemail = ['agite@apollo.com']

    tmp_file = r'c:/temp/df.png'

    import os

    df = pd.DataFrame({'a': [1, 24, 5, 6], 'b': ['test', 'pfa', np.nan, 'last row']})
    fig = df.plot().get_figure()
    fig.savefig(tmp_file)

    # email example 1: df as attachment
    email_attachment(df, fromemail, toemail, 'test email', 'email example1: df as attachment', filetypes='xlsx')
    # email example 2: df as html
    email_attachment(df, fromemail, toemail, 'test email', 'email example2: df as html', filetypes='html')
    # email example 3: df plot as inline image
    email_attachment(fig, fromemail, toemail, 'test email', 'email example3: df as plt inline', filetypes='plt')
    # email example 4: image file as inline image
    email_attachment(tmp_file, fromemail, toemail, 'test email', 'email example4: df as png inline', filetypes='png')
    # email example 5: df as html and inline image
    email_attachment([df, tmp_file], fromemail, toemail, 'test email', 'email example5: df as html and png inline',
                     filetypes=['html', 'png'])
    # email example 6: df as attachment and inline image
    email_attachment([df, tmp_file], fromemail, toemail, 'test email',
                     'email example6: df as attachment and png inline', filetypes=['xlsx', 'png'])
    # email example 7: df as html and inline image with titles
    email_attachment([df, tmp_file], fromemail, toemail, 'test email',
                     'email example7: df as html and png inline with titles',
                     attachment_titles=['table1', 'chart1'],
                     filetypes=['html', 'png'])

    os.remove(tmp_file)

    # older function
    emaildfs([df, df], fromemail, toemail, 'test email', ['t1', 't2'],
             color='green_dark',
             font_size='tiny',
             text_align='center')
