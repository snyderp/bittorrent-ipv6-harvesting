import requests
import random
import struct
import socket
import binascii
import bencode
import hashlib
import ipaddress
import urlparse
import spider

def udp_create_connection_request():
    connection_id = 0x41727101980 #default connection id
    action = 0x0 #action (0 = give me a new connection id)
    transaction_id = udp_get_transaction_id()
    buf = struct.pack("!q", connection_id) #first 8 bytes is connection id
    buf += struct.pack("!i", action) #next 4 bytes is action
    buf += struct.pack("!i", transaction_id) #next 4 bytes is transaction id
    return (buf, transaction_id)

def udp_get_transaction_id():
    return int(random.randrange(0, 255))

def udp_create_announce_request(connection_id, torrent_hash, ip_address=None):

    # Taken from http://www.bittorrent.org/beps/bep_0015.html
    action = 0x1 #action (1 = announce)
    transaction_id = udp_get_transaction_id()
    buf = struct.pack("!q", connection_id) #first 8 bytes is connection id
    buf += struct.pack("!i", action) #next 4 bytes is action
    buf += struct.pack("!i", transaction_id) #followed by 4 byte transaction id

    # Add torrent hash
    buf += struct.pack("!20s", binascii.b2a_hex(torrent_hash))
    buf += struct.pack("!20s", generate_peer_id()) # Add peer_id

    buf += struct.pack("!q", 0) # download value
    buf += struct.pack("!q", 0) # left value
    buf += struct.pack("!q", 0) # uploaded value

    buf += struct.pack("!i", 0) # event value
    if ip_address:
        ip_add_obj = ipaddress.ip_address(unicode(ip_address))
        buf += struct.pack("!16s", binascii.b2a_hex(ip_add_obj.packed))
    else:
        buf += struct.pack("!q2", 0)

    buf += struct.pack("!i", 0) # key value value
    buf += struct.pack("!i", 50) # num_want value
    buf += struct.pack("!h", 9999) # port value

    return (buf, transaction_id)


def udp_create_announce_response(buf, sent_transaction_id, torrent_hash):
    buf_len = len(buf)
    if buf_len < 16:
        raise RuntimeError("Wrong response length while scraping: %s" % len(buf))
    action = struct.unpack_from("!i", buf)[0] #first 4 bytes is action

    # next 4 bytes is transaction id
    res_transaction_id = struct.unpack_from("!i", buf, 4)[0]
    if res_transaction_id != sent_transaction_id:
        raise RuntimeError("Transaction ID doesnt match in scrape response! Expected %s, got %s" % (sent_transaction_id, res_transaction_id))

    ip6s = []

    if action == 0x1:
        offset = 20
        while offset < buf_len:
            ip = buf[offset:offset+16]
            # port = struct.unpack_from("!h", buf, offset+16)[0]
            ip6s.append(ipaddress.ip_address(ip))
            offset += 18

        return ip6s
    else:
        #an error occured, try and extract the error string
        error = struct.unpack_from("!s", buf, 8)
        raise RuntimeError("Error while scraping: %s" % error)


def udp_parse_connection_response(buf, sent_transaction_id):
    if len(buf) < 16:
        raise RuntimeError("Wrong response length getting connection id: %s" % len(buf))
    action = struct.unpack_from("!i", buf)[0] #first 4 bytes is action

    res_transaction_id = struct.unpack_from("!i", buf, 4)[0] #next 4 bytes is transaction id
    if res_transaction_id != sent_transaction_id:
        raise RuntimeError("Transaction ID doesnt match in connection response! Expected %s, got %s"
            % (sent_transaction_id, res_transaction_id))

    if action == 0x0:
        connection_id = struct.unpack_from("!q", buf, 8)[0] #unpack 8 bytes from byte 8, should be the connection_id
        return connection_id
    elif action == 0x3:
        error = struct.unpack_from("!s", buf, 8)
        raise RuntimeError("Error while trying to get a connection response: %s" % error)
    pass


ip_cache = {}

def udp_announce(tracker, torrent_hash, ip_address=None):
    parsed_tracker = urlparse.urlparse(tracker)
    transaction_id = "\x00\x00\x04\x12\x27\x10\x19\x70"
    connection_id = "\x00\x00\x04\x17\x27\x10\x19\x80"
    sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    sock.settimeout(8)
    key = "{0}:{1}".format(parsed_tracker.hostname, parsed_tracker.port)
    try:
        dest_ip, dest_port = ip_cache[key]
    except KeyError:
        ip_data = socket.getaddrinfo(parsed_tracker.hostname, parsed_tracker.port)
        dest_ip = None
        dest_port = None
        for family, socktype, proto, canonname, sockaddr in ip_data:
            if family == 10:
                address, port, flow_info, scope_id = sockaddr
                dest_ip = address
                dest_port = port
                break
        if not dest_ip:
            raise Exception("Unable to find IPv6 address for {0}".format(parsed_tracker.hostname))
        ip_cache[key] = dest_ip, dest_port

    conn = (dest_ip, dest_port)
    #Get connection ID
    req, transaction_id = udp_create_connection_request()
    sock.sendto(req, conn);
    buf = sock.recvfrom(2048)[0]
    connection_id = udp_parse_connection_response(buf, transaction_id)

    #Scrape away
    req, transaction_id = udp_create_announce_request(connection_id, torrent_hash, ip_address=ip_address)
    sock.sendto(req, conn)
    buf = sock.recvfrom(2048)[0]
    return udp_create_announce_response(buf, transaction_id, torrent_hash)

def http_announce(tracker, info_hash, ip_address=None, size=None):
    ipv6_addresses = []

    peer_id = generate_peer_id()
    left = size or 0
    params = {'info_hash': info_hash, 'peer_id': peer_id, 'port': '6889',
              'uploaded': left/2, 'downloaded': left/2, 'left': left,
              'numwant': 25, 'compact': '1'}

    if ip_address:
        params['ipv6'] = ip_address

    try:
        r = requests.get(tracker, params=params, timeout=10)
        response = bencode.bdecode(r.content)
    except bencode.BTL.BTFailure, e:
        print " ! Bad Response: {0}".format(e.message)
        return ipv6_addresses
    except requests.exceptions.ConnectionError, e:
        print " ! Error connecting to: {0}".format(t)
        return ipv6_addresses
    except requests.exceptions.Timeout, e:
        print " ! Timeout connecting to: {0}".format(t)
        return ipv6_addresses

    if "failure reason" in response:
        print r.url
        print " ! {0}".format(response["failure reason"])
        return ipv6_addresses

    if 'peers6' in response:
        offset = 0
        peers6_len = len(response['peers6'])
        while offset < peers6_len:
            an_ip_address = response['peers6'][offset:offset + 16]
            # a_port = response['peers6'][offset + 16:offset + 18]
            offset += 18
            ipv6_addresses.append(ipaddress.ip_address(an_ip_address))
    return ipv6_addresses

def ips_for_tracker(**kwargs):
    """Fetches all the IP addresses used to seed a tracker represented by
    a magnet uri.


    Return:
        A pair of lists, first containing all ipv4 addresses seeding the
        tracker, and second containing all the ipv6 addresses seeding the
        tracker.  Addresses are represented by IPv4Address and IPv6Address
        instances, respectivly.
    """
    tracker = kwargs['tracker']
    info_hash = kwargs['hash']

    try:
        ip_address = kwargs['ip']
    except KeyError:
        ip_address = None

    parsed_tracker = urlparse.urlparse(tracker)
    if parsed_tracker.scheme == "udp":
        try:
            return udp_announce(tracker, info_hash, ip_address=ip_address)
        except Exception, e:
            if "Unable to find IPv6 address" not in e.message:
              raise e
            print " ! {0}".format(e.message)
            return []
    elif parsed_tracker.scheme in ["http", "https"]:
        return http_announce(tracker, info_hash, ip_address=ip_address)

def parse_torrent(torrent_path):
    """Reads a tracker from disk and returns metadata information about it,
    including a list of trackers for the torrent and the hash for the torrent.

    Args:
        torrent_path -- A path to read a torrent file from on disk

    Returns:
        A tuple of three values, 1) a list of zero or more trackers,
        2) the info hash for the files in the tracker, and 3) the total size of
        the files in the torrent
    """
    trackers = []
    size = 0
    info_hash = None
    with open(torrent_path, 'r') as h:
        torrent_data = h.read()
        data = bencode.bdecode(torrent_data)
        trackers.append(data['announce'])

        try:
            trackers += [t[0] for t in data['announce-list']]
        except KeyError:
            pass

        info_hash = hashlib.sha1(bencode.bencode(data['info'])).digest()
        try:
            for files_data in data['info']['files']:
                size += files_data['length']
        except KeyError:
            size = data['info']['length']

    return trackers, info_hash, size

def parse_magnet_uri(uri):
    parsed_uri = urlparse.urlparse(uri)
    params = urlparse.parse_qs(parsed_uri.query or parsed_uri.path[1:])
    try:
        trackers = params['tr']
    except KeyError:
        trackers = None

    try:
        info_hash = binascii.a2b_hex(params['xt'][0].replace("urn:btih:", ''))
    except KeyError:
        info_hash = None
    return trackers, info_hash, 0


def generate_peer_id():
    return '-PS1234-' + ''.join(chr(random.randint(0,255)) for i in range(12))

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description='Fetch IP addresses feeding a magetnet link.')
    parser.add_argument('--output', default=None, help="If provided, a path to write found addresses to.")
    parser.add_argument('--magnetpages', default=None, help="A file to read from, with urls to fetch magnet urls from.")
    parser.add_argument('--magnetpage', default=None, help="A URL to scape for magnet urls")
    parser.add_argument('--hash')
    parser.add_argument('--tracker')
    parser.add_argument('--magnet', default=None, help="A magenet link to parse for torrent information.")
    parser.add_argument('--torrent', default=None, help="Path on disk to a torrent file to parse for information.")
    parser.add_argument('--ip', help="The IPv6 address to report announcing from.", default=None)
    args = parser.parse_args()

    total_ips = {}

    output = open(args.output, 'aw') if args.output else sys.stdout

    if args.magnetpages:
        magnet_urls = []
        with open(args.magnetpages, 'r') as h:
            for line in h:
                local_urls = spider.magnet_uris_on_url(line.strip()) 
                print " * Found {0} urls in {1}".format(len(local_urls), line)
                magnet_urls += local_urls
        work = [parse_magnet_uri(murl) for murl in set(magnet_urls)]
    elif args.magnetpage:
        magnet_urls = spider.magnet_uris_on_url(args.magnetpage)
        work = [parse_magnet_uri(murl) for murl in magnet_urls]
    elif args.magnet:
        work = [parse_magnet_uri(args.magnet)]
    elif args.torrent:
        work = [parse_torrent(args.torrent)]
    else:
        trackers = [args.tracker]
        info_hash = args.hash
        size = 0
        work = [(trackers, info_hash, size)]

    for trackers, info_hash, size in work:
        for t in trackers:
            print "Annoucing for {0} on {1}".format(binascii.b2a_hex(info_hash), t)
            found_addrs = ips_for_tracker(hash=info_hash, tracker=t, ip=args.ip)
            print " * Found {0} addresses".format(len(found_addrs))
            for a in found_addrs:
                if a.exploded not in total_ips:
                    total_ips[a.exploded] = True
                    output.write(a.exploded + "\n")

