# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

#!/usr/bin/env python

import sys
import re
import mailbox
import string
import rfc822
import time
import datetime
import hgapi
import urllib2
import simplejson as json

m_i = hgapi.Repo('/home/froydnj/src/mozilla-inbound/')
m_i_json_pushes_url = "http://hg.mozilla.org/integration/mozilla-inbound/json-pushes?fromchange=%s&tochange=%s"
m_i_pushloghtml = "http://hg.mozilla.org/integration/mozilla-inbound/pushloghtml?fromchange=%s&tochange=%s"
m_i_rev = "http://hg.mozilla.org/integration/mozilla-inbound/rev/%s"

subject_percent_change_re = re.compile("^Talos (?:Regression|Improvement).*?(de|in)crease ([0-9]+(?:\\.[0-9]+(?:e\\+[0-9]+)?)?)%", re.DOTALL)
changeset_range_re = re.compile(r"Changeset range: http://hg.mozilla.org/integration/mozilla-inbound/pushloghtml\?fromchange=([0-9a-f]{12,})&tochange=([0-9a-f]{12,})")

platforms = ['XP', 'Win7', 'MacOSX 10.6 (rev4)', 'Linux x64', 'Linux', 'WINNT 5.2', 'WINNT 6.1',
             'CentOS release 5 (Final)', 'CentOS (x86_64) release 5 (Final)', 'MacOSX 10.7']
tests = ['Ts, MED Dirty Profile',
         'Ts, MAX Dirty Profile',
         'Ts, Paint',
         'Ts Paint, MED Dirty Profile',
         'Ts Paint, MAX Dirty Profile']

msg_template = string.Template("${strtime} ${sign}${amount}% from ${fromchange} to ${tochange}")

class Revision:
    def __init__(self, node_id):
        self.node_id = node_id
    def __eq__(self, other):
        for i in range(9):
            if self.date[i] != other.date[i]:
                return False
        return True
    def __ne__(self, other):
        for i in range(9):
            if self.date[i] != other.date[i]:
                return True
        return False
    def __lt__(self, other):
        return self.date < other.date
    def __gt__(self, other):
        return other.date < self.date
    def __le__(self, other):
        return not other.date < self.date
    def __ge__(self, other):
        return not self.date < other.date
    def same_node(self, other):
        return self.node_id == other.node_id
    def __str__(self):
        return self.node_id

class ChangeInformation:
    def __init__(self, deltas, fromchange, tochange):
        if type(fromchange) == str or type(fromchange) == unicode:
            self.fromchange = Revision(fromchange)
        else:
            self.fromchange = fromchange
        if type(tochange) == str or type(tochange) == unicode:
            self.tochange = Revision(tochange)
        else:
            self.tochange = tochange
        self.deltas = deltas
    def __str__(self):
        fromdate = time.strftime("%Y-%m-%d %H:%M:%S", self.fromchange.date)
        todate = time.strftime("%Y-%m-%d %H:%M:%S", self.tochange.date)
        s = "%s (%s):%s (%s)" % (self.fromchange.node_id, fromdate, self.tochange.node_id, todate)
        for x in self.deltas:
            s += " " + str(x)
        return s

class TalosDelta:
    def __init__(self, sign, amount, platform):
        self.sign = sign
        self.amount = amount
        self.platform = platform
    def __eq__(self, other):
        return other is not None and self.platform == other.platform
    def __ne__(self, other):
        return self.platform != other.platform
    def __hash__(self):
        return hash(self.platform)
    def __str__(self):
        return "%s: %s%s" % (self.platform, self.sign, self.amount)
    def for_platform(self, p):
        return self.platform == p

def grovel_message_information(msg, platform):
    subject = msg.get('Subject')
    match = subject_percent_change_re.search(subject)
    if match is None:
        print >>sys.stdout, subject, 'did not match!'
        assert match is not None
    sign = { 'de': '-', 'in': '+' }[match.group(1)]
    amount = float(match.group(2))

    date = msg.get('Date')
    parsed = rfc822.parsedate(date)

    assert not msg.is_multipart()
    body = msg.get_payload()

    match = changeset_range_re.search(body)
    assert match is not None
    fromchange = match.group(1)
    tochange = match.group(2)

    if fromchange == tochange:
        # Bizarre.  Skip this.
        return None

    deltas = set()
    deltas.add(TalosDelta(sign, amount, platform))

    ci = ChangeInformation(deltas, fromchange, tochange)

    m_i_url = m_i_json_pushes_url % (fromchange, tochange)
    json_stream = urllib2.urlopen(m_i_url)
    json_pushes = json.loads(json_stream.read())

    # You might think the json information comes back in sorted revision order.
    # You would be wrong.
    json_items = json_pushes.items()
    json_items.sort(key=lambda x: x[0])
    first = json_items[0]
    last = json_items[-1]
    ci.fromchange.date = time.gmtime(first[1]['date'])
    ci.tochange.date = time.gmtime(last[1]['date'])

    return ci

def parse_date_range(mutt_date_desc):
    date_re = r"(\d{2})/(\d{2})/(\d{4})"
    m = re.match(date_re + "-" + date_re, mutt_date_desc)
    if m is None:
        raise BaseException, "Cannot parse date range specification"

    start_date = datetime.datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    end_date = datetime.datetime(int(m.group(6)), int(m.group(5)), int(m.group(4))) + datetime.date.resolution
    return (start_date, end_date)

def relevant_messages(mbox, date_range, talos_test):
    global platforms
    tree_of_interest = "Mozilla-Inbound(-Non-PGO)?"
    (begin_date, end_date) = parse_date_range(date_range)
    test_of_interest = re.escape(talos_test)
    platform_of_interest = '|'.join([re.escape(p) for p in platforms])

    platform_tree_test = re.compile("^Talos (?:Regression|Improvement).*?" + test_of_interest + r" (?:in|de)crease.*?(" + platform_of_interest + ") " + tree_of_interest + "$")
    subject_trans_table = string.maketrans("\t", " ")

    for msg in mbox.itervalues():
        to = msg.get('To')
        if to is None:
            continue

        if not to.startswith('dev-tree-management@'):
            continue

        subject = msg.get('Subject')
        if subject is None:
            continue

        subject = subject.translate(subject_trans_table, "\n")
        match = platform_tree_test.search(subject)
        if match is not None:
            matched_platform = match.group(1)
            non_pgo = match.group(2)
            if non_pgo is None:
                matched_platform += "-PGO"
            # This is a little silly.
            msg_date = datetime.datetime.fromtimestamp(time.mktime(rfc822.parsedate(msg.get('Date'))))
            if (begin_date < msg_date) and (msg_date < end_date):
                yield msg, matched_platform

def merge_deltas(x, y):
    deltas = set()
    intersection = x.deltas & y.deltas
    if None and len(intersection) != 0:
        print x.fromchange
        print x.tochange
        print 'x entries:'
        for i in x.deltas:
            print i
        print y.fromchange
        print y.tochange
        print 'y entries:'
        for i in y.deltas:
            print i
        raise BaseException, "OUCH"
    deltas.update(x.deltas)
    deltas.update(y.deltas)
    return deltas

#    |---SMALLER---|
# |-------LARGER-------|
def subsumed_three_way_split(smaller, larger):
    lower = ChangeInformation(larger.deltas, larger.fromchange, smaller.fromchange)
    mid_deltas = merge_deltas(larger, smaller)
    middle = ChangeInformation(mid_deltas, smaller.fromchange, smaller.tochange)
    upper = ChangeInformation(larger.deltas, smaller.tochange, larger.tochange)
    return [lower, middle, upper]

#     |---UPPER------------|
# |-----------LOWER---|
def offset_three_way_split(lower, upper):
    bottom = ChangeInformation(lower.deltas, lower.fromchange, upper.fromchange)
    mid_deltas = merge_deltas(lower, upper)
    middle = ChangeInformation(mid_deltas, upper.fromchange, lower.tochange)
    top = ChangeInformation(upper.deltas, lower.tochange, upper.tochange)
    return [bottom, middle, top]

def insert_three_way_split_into_list(i, global_list, split):
    #global_list[i] = split[1]
    #insert_info_into_list(split[0], global_list)
    #insert_info_into_list(split[2], global_list)
    global_list[i:i+1] = split

def insert_info_into_list(info, global_list):
    n = len(global_list)

    for i in range(n):
        point = global_list[i]

        # Every revision is less than the current point
        if info.tochange < point.fromchange:
            global_list.insert(i, info)
            return
        # Every revision is more than the current point
        if info.fromchange > point.tochange:
            continue

        # Now the interesting cases
        if info.fromchange > point.fromchange:
            if info.tochange < point.tochange:
                #     |-----INFO----|
                # |--------POINT---------|
                split = subsumed_three_way_split(info, point)
                insert_three_way_split_into_list(i, global_list, split)
                return
            elif info.tochange == point.tochange:
                # Unlikely, but merge the platform information for these.
                point.deltas = merge_deltas(info, point)
                return
            else:
                #      |-------INFO--------|
                # |--------POINT------|
                split = offset_three_way_split(point, info)
                insert_three_way_split_into_list(i, global_list, split)
                return
        elif info.fromchange == point.fromchange:
            if info.tochange < point.tochange:
                # |-----INFO----|
                # |-------POINT------|
                lower = ChangeInformation(merge_deltas(info, point),
                                          info.fromchange, info.tochange)
                upper = ChangeInformation(point.deltas, info.tochange, point.tochange)
                global_list[i] = lower
                insert_info_into_list(upper, global_list)
                return
            elif info.tochange == point.tochange:
                # Unlikely, but merge the platform information for these.
                point.deltas = merge_deltas(info, point)
                return
            else:
                # |---------INFO-----------|
                # |----POINT----|
                lower = ChangeInformation(merge_deltas(info, point),
                                          point.fromchange, point.tochange)
                upper = ChangeInformation(info.deltas, point.tochange, info.tochange)
                # Yes, really.
                if point.fromchange == point.tochange:
                    global_list[i:i+1] = [lower, upper]
                else:
                    global_list[i] = lower
                    insert_info_into_list(upper, global_list)
                return
        elif info.tochange > point.tochange:
            assert info.fromchange < point.fromchange
            # |--------INFO-----------|
            #    |-----POINT-----|
            split = subsumed_three_way_split(point, info)
            insert_three_way_split_into_list(i, global_list, split)
            return
        elif info.tochange == point.tochange:
            assert info.fromchange < point.fromchange
            # |-------INFO--------|
            #     |---POINT-------|
            lower = ChangeInformation(info.deltas, info.fromchange, point.fromchange)
            upper = ChangeInformation(merge_deltas(info, point),
                                      point.fromchange, point.tochange)
            #global_list[i] = upper
            #insert_info_into_list(lower, global_list)
            global_list[i:i+1] = [lower, upper]
            return
        else:
            # |--------INFO------|
            #      |------POINT------|
            split = offset_three_way_split(info, point)
            insert_three_way_split_into_list(i, global_list, split)
            return

    # More than everything in the list!
    global_list.append(info)

def collect_platforms(changes):
    platforms = set()
    for c in changes:
        for d in c.deltas:
            platforms.add(d.platform)
    return platforms

header_row_template = string.Template('<tr><th>Pushlog</th>${headers}</tr>')

def output_header_row(platforms):
    mapping = { 'headers': '\n'.join(['<th>%s</th>' % p for p in platforms]) }
    return header_row_template.substitute(mapping)

def url_for_change(change):
    if change.fromchange == change.tochange:
        return m_i_rev % change.fromchange
    else:
        return m_i_pushloghtml % (change.fromchange, change.tochange)

cell_template = string.Template('<td${style}${rowspan}>${sign}${amount}</td>')

class TableChangeCell:
    def __init__(self, platform, delta):
        self.platform = platform
        self.delta = delta
        self.rowspan = 1
    def output_html(self):
        mapping = { 'style': '',
                    'rowspan': '',
                    'sign': '',
                    'amount': '' }
        if self.delta is not None:
            style = ' style="background:#6666ff"'
            if self.delta.sign == '+':
                style = ' style="background:red"'
            mapping['style'] = style
            if self.rowspan > 1:
                mapping['rowspan'] = ' rowspan="%s"' % self.rowspan
            mapping['sign'] = self.delta.sign
            mapping['amount'] = self.delta.amount
        return cell_template.substitute(mapping)

row_template = string.Template('<tr>${cells}</tr>')

class TableChangeRow:
    def __init__(self, fromchange, tochange):
        self.fromchange = fromchange
        self.tochange = tochange
        self.cells = []
    def add_cell(self, platform, delta):
        self.cells.append(TableChangeCell(platform, delta))
    def cell_for_platform(self, platform):
        for c in self.cells:
            if platform == c.platform:
                return c
        return None
    def output_html(self):
        url = m_i_pushloghtml % (self.fromchange, self.tochange)
        tds = ['<td><a href="%s">%s to %s</a></td>' % (url, self.fromchange, self.tochange)]
        tds.extend([c.output_html() for c in self.cells])
        return row_template.substitute({ 'cells': '\n'.join(tds) })

def try_increase_rowspan_of_previous_cell(rows, platform, delta):
    for r in reversed(rows):
        cell = r.cell_for_platform(platform)
        if cell is not None:
            if cell.delta == delta:
                cell.rowspan += 1
                return True
            return False
    return False

def build_table_structure(platforms, changes):
    table_rows = []
    for c in changes:
        current = TableChangeRow(c.fromchange, c.tochange)
        for p in platforms:
            inserted_cell = False
            for d in c.deltas:
                if d.for_platform(p):
                    # Try to make the cells maximally large for any
                    # given delta.  See if this ought to combine with
                    # some previous row.
                    if not try_increase_rowspan_of_previous_cell(table_rows, p, d):
                        current.add_cell(p, d)
                    inserted_cell = True
                    break
            if not inserted_cell:
                current.add_cell(p, None)
        table_rows.append(current)
    return table_rows

html_page_template = string.Template("""
<html>
<head>
  <title>Summary of changes for ${test} over ${date_range}</title>
</head>
<body>
<h1>Summary of changes for ${test} over ${date_range}</h1>
<table border="1">
${table}
</table>
</body>
</html>""")

def output_html_for(changes, date_range, talos_test):
    platforms = collect_platforms(changes)
    platforms = [x for x in platforms]
    platforms.sort()

    rows = [output_header_row(platforms)]
    structure = build_table_structure(platforms, changes)
    rows.extend([r.output_html() for r in structure])

    return html_page_template.substitute({ 'test': talos_test,
                                           'date_range': date_range,
                                           'table': '\n'.join(rows) })

all_talos_test_descriptions = [ 'Ts, MED Dirty Profile',
                                'Ts, MAX Dirty Profile',
                                'SVG, Opacity Row Major',
                                'Dromaeo (DOM)',
                                'Dromaeo (CSS)',
                                'SunSpider 2 MozAfterpaint',
                                'DHTML Row Major MozAfterPaint',
                                'DHTML 2 MozAfterPaint',
                                'Ts Shutdown, MAX Dirty Profile',
                                'Ts Shutdown, MED Dirty Profile',
                                'V8',
                                'Paint',
                                'tscroll',
                                'Number of Constructors',
                                'Tp5 No Network Row Major MozAfterPaint',
                                'Tp5 No Network Row Major MozAfterPaint (Private Bytes)',
                                'Tp5 No Network Row Major MozAfterPaint (Main RSS)',
                                'Tp5 No Network Row Major MozAfterPaint (Content RSS)',
                                'Tp5 No Network Row Major MozAfterPaint (%CPU)',
                                'Trace Malloc MaxHeap',
                                'Trace Malloc Allocs',
                                'Trace Malloc Leaks',
                                'a11y Row Major MozAfterPaint',
                                'Ts, Paint',
                                'Robocop Pan Benchmark',
                                'Robocopy Checkerboarding No Snapshot Benchmark',
                                'Robocop Checkerboarding Real User Benchmark' ]

def talos_test_to_filename(talos_test):
    tt = string.maketrans(" ", "-")
    return string.translate(talos_test, tt, ",()").lower() + ".html"

def digest_mailbox_to_summary(mbox, date_range, talos_test):
    interesting_changes = []
    n_emails_sent = 0

    for (msg, platform) in relevant_messages(mbox, date_range, talos_test):
        n_emails_sent += 1
        info = grovel_message_information(msg, platform)
        if info is None:
            continue
        insert_info_into_list(info, interesting_changes)

    # Cleanup by removing from == to changes.
    interesting_changes = [c for c in interesting_changes if not c.fromchange.same_node(c.tochange)]

    if len(interesting_changes) == 0:
        return 0, n_emails_sent

    # Cleanup by merging changes with identical fromchanges.
    temp = [interesting_changes[0]]
    for c in interesting_changes[1:]:
        last = temp[-1]
        if c.fromchange != last.fromchange:
            temp.append(c)
            continue
        if c.tochange > last.tochange:
            last.tochange = c.tochange
        last.deltas = merge_deltas(c, last)
    interesting_changes = temp

    if len(interesting_changes) > 0:
        with open(talos_test_to_filename(talos_test), 'w') as f:
            print >>f, output_html_for(interesting_changes, date_range, talos_test)

    n_regression_ranges = len(interesting_changes)
    return n_regression_ranges, n_emails_sent

def main(argv):
    mbox = mailbox.mbox(argv[0])
    for test in all_talos_test_descriptions:
        n_ranges, n_emails = digest_mailbox_to_summary(mbox, argv[1], test)
        print '%s: %d ranges, %d emails' % (test, n_ranges, n_emails)

if __name__ == '__main__':
    main(sys.argv[1:])
