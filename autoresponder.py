import argparse
import datetime
import json
import os
import os.path
import sys
import time

from bs4 import BeautifulSoup

from mastodon import Mastodon

class Config:
    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))

        with open(self.path) as f:
            self.from_dict(json.load(f))

    def from_dict(self, json):
        self.base_url = json['base_url']
        self.client_id = json['client_id']
        self.client_secret = json['client_secret']
        self.access_token = json['access_token']

        self.admins = json['admins']
        self.message = json['message'] + ''.join(' @' + admin for admin in self.admins)

        self.state_file = json['state_file']


def get_api(config):
    return Mastodon(client_id=config.client_id,
        client_secret=config.client_secret,
        api_base_url=config.base_url,
        access_token=config.access_token)


def html_to_text(html):
    soup = BeautifulSoup(html, 'html.parser')
    lines = []
    for p in soup('p'):
        lines.append(p.text)
    return '\n'.join(lines)


def sanitize_forwarded_toot(text):
    # removing potentially unwanted mentions
    return text.replace('@', '/')


def split_into_toots(prefix, text):
    toot_len = 500
    part_len = toot_len - len(prefix) - 3

    # (len(text) + (part_len - 1)) // part_len
    # == ceil(len(text) / part_len)
    for i in range((len(text) + (part_len - 1)) // part_len):
        if part_len * (i + 1) >= len(text):
            # last part
            yield '{}\n{}'.format(prefix,
                text[part_len*i:part_len*(i+1)])
        else:
            yield '{}\n{}\n…'.format(prefix,
                text[part_len*i:part_len*(i+1)])

def run_bot(config):
    api = get_api(config)

    last_notification = -1
    if os.path.exists(config.state_file):
        with open(config.state_file) as f:
            last_notification = int(f.read())

    with open(config.state_file, 'a') as state_file:
        while True:
            notifications = api.notifications()
            ln_changed = False

            if isinstance(notifications, dict) and ('error' in notifications):
                raise Exception('API error: {}'.format(notifications['error']))

            if last_notification == -1:
                # if this is the first time the bot is running, don't autorespond
                # retroactively
                if len(notifications) > 0:
                    last_notification = notifications[0]['id']
                else:
                    last_notification = 0
                ln_changed = True
            else:
                # reversed order to process notification in chronological order
                for notification in notifications[::-1]:
                    if notification['id'] <= last_notification:
                        continue
                    if notification['type'] != 'mention':
                        continue

                    sender = notification['status']['account']['acct']

                    if sender in config.admins:
                        if notification['status']['visibility'] != 'public' :
                            continue
                        # We received a public announcement from admins, we should boost it
                        api.status_reblog(notification['status']['id'])
                    else:
                        response = '@{} {}'.format(
                            sender,
                            config.message)

                        response_sent = api.status_post(response,
                            in_reply_to_id=notification['status']['id'],
                            visibility='direct')

                        if ((notification['status']['visibility'] != 'public') and
                           (len(config.admins) > 0)):
                            # the bot was sent a DM, we should forward that too
                            text = html_to_text(notification['status']['content'])
                            text = sanitize_forwarded_toot(text)

                            recipient_prefix = ' '.join('@'+x for x in config.admins + [sender])
                            prev_part_id = response_sent['id']
                            for part in split_into_toots(recipient_prefix, text):
                                part_sent = api.status_post(part,
                                    in_reply_to_id=prev_part_id,
                                    visibility='direct')
                                prev_part_id = part_sent['id']

                    print('Responded to status {} from {}.'.format(
                        notification['status']['id'],
                        notification['status']['account']['acct']))

                    last_notification = notification['id']
                    ln_changed = True


            if ln_changed:
                state_file.seek(0)
                state_file.truncate(0)
                state_file.write(str(last_notification))
                state_file.flush()

            time.sleep(60)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument('-c', '--config', help='File to load the config from.',
        default='config.json')

    args = parser.parse_args()

    config = Config(args.config)

    run_bot(config)

if __name__ == '__main__':
    main()
