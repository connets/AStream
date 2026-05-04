"""Module for reading DASH MPD files.

Author: Parikshit Juluri
Contact : pjuluri@umkc.edu
"""
import math
import re
import urllib.parse

import config_dash

FORMAT = 0
URL_LIST = list()
# Dictionary to convert size to bits
SIZE_DICT = {'bits':   1,
             'Kbits':  1024,
             'Mbits':  1024*1024,
             'bytes':  8,
             'KB':  1024*8,
             'MB': 1024*1024*8,
             }
# Try to import the C implementation of ElementTree which is faster
# In case of ImportError import the pure Python implementation
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

MEDIA_PRESENTATION_DURATION = 'mediaPresentationDuration'
MIN_BUFFER_TIME = 'minBufferTime'
DASH_TEMPLATE_PATTERN = re.compile(
    r'\$(?P<identifier>RepresentationID|Number|Bandwidth|Time)'
    r'(?P<format>%0?\d*d)?\$'
)
LEGACY_NUMBER_TEMPLATE_PATTERN = re.compile(r'\$Number\$(%0?\d*d)')
ISO_DURATION_PATTERN = re.compile(
    r'^P'
    r'(?:(?P<days>\d+(?:\.\d+)?)D)?'
    r'(?:T'
    r'(?:(?P<hours>\d+(?:\.\d+)?)H)?'
    r'(?:(?P<minutes>\d+(?:\.\d+)?)M)?'
    r'(?:(?P<seconds>\d+(?:\.\d+)?)S)?'
    r')?$'
)

VIDEO_CODECS = ('avc', 'hev', 'hvc', 'dvh', 'vp8', 'vp9', 'av01')
AUDIO_CODECS = ('mp4a', 'ac-3', 'ec-3', 'opus', 'vorbis')


def _log(level, message):
    logger = getattr(config_dash, 'LOG', None)
    if logger:
        getattr(logger, level)(message)


def get_tag_name(xml_element):
    """ Module to remove the xmlns tag from the name
        eg: '{urn:mpeg:dash:schema:mpd:2011}SegmentTemplate'
             Return: SegmentTemplate
    """
    try:
        tag_name = xml_element[xml_element.find('}') + 1:]
    except TypeError:
        _log("error", "Unable to retrieve the tag.")
        return None
    return tag_name


def get_playback_time(playback_duration):
    """Get the playback time in seconds from an ISO-8601 duration."""
    if playback_duration is None:
        return None

    match = ISO_DURATION_PATTERN.match(playback_duration)
    if match:
        parts = dict((key, float(value) if value else 0)
                     for key, value in match.groupdict().items())
        return (parts['days'] * 24 * 60 * 60 +
                parts['hours'] * 60 * 60 +
                parts['minutes'] * 60 +
                parts['seconds'])

    numbers = re.split('[PTHMS]', playback_duration)
    numbers = [value for value in numbers if value != '']
    numbers.reverse()
    total_duration = 0
    for count, val in enumerate(numbers):
        if count == 0:
            total_duration += float(val)
        elif count == 1:
            total_duration += float(val) * 60
        elif count == 2:
            total_duration += float(val) * 60 * 60
    return total_duration


def _children(element, tag_name=None):
    if tag_name is None:
        return list(element)
    return [child for child in element if get_tag_name(child.tag) == tag_name]


def _first_child(element, tag_name):
    for child in element:
        if get_tag_name(child.tag) == tag_name:
            return child
    return None


def _first_child_text(element, tag_name):
    child = _first_child(element, tag_name)
    if child is not None and child.text:
        return child.text.strip()
    return ''


def _join_url(base_url, url):
    if not url:
        return base_url or ''
    return urllib.parse.urljoin(base_url or '', url.strip())


def _merge_base_url(parent_base_url, element):
    return _join_url(parent_base_url, _first_child_text(element, 'BaseURL'))


def _read_segment_timeline(segment_template):
    timeline = _first_child(segment_template, 'SegmentTimeline')
    if timeline is None:
        return None
    return [dict(segment.attrib) for segment in _children(timeline, 'S')]


def _read_segment_template(element):
    segment_template = _first_child(element, 'SegmentTemplate')
    if segment_template is None:
        return None
    return {
        'attributes': dict(segment_template.attrib),
        'timeline': _read_segment_timeline(segment_template)
    }


def _copy_segment_template(segment_template):
    if not segment_template:
        return None
    timeline = segment_template.get('timeline')
    return {
        'attributes': dict(segment_template.get('attributes', {})),
        'timeline': [dict(item) for item in timeline] if timeline else timeline
    }


def _merge_segment_template(parent, child):
    if not parent:
        return _copy_segment_template(child)
    if not child:
        return _copy_segment_template(parent)

    merged = _copy_segment_template(parent)
    merged['attributes'].update(child.get('attributes', {}))
    if child.get('timeline') is not None:
        merged['timeline'] = [dict(item) for item in child['timeline']]
    return merged


def _read_segment_list(element):
    segment_list = _first_child(element, 'SegmentList')
    if segment_list is None:
        return None

    initialization = _first_child(segment_list, 'Initialization')
    source_url = None
    if initialization is not None:
        source_url = initialization.attrib.get('sourceURL')

    return {
        'attributes': dict(segment_list.attrib),
        'initialization': source_url,
        'segment_urls': [dict(segment_url.attrib)
                         for segment_url in _children(segment_list, 'SegmentURL')]
    }


def _read_segment_base_initialization(element):
    segment_base = _first_child(element, 'SegmentBase')
    if segment_base is None:
        return None

    initialization = _first_child(segment_base, 'Initialization')
    if initialization is None:
        return None
    return initialization.attrib.get('sourceURL')


def _media_type(adaptation_set, representation):
    values = [
        adaptation_set.attrib.get('contentType', ''),
        adaptation_set.attrib.get('mimeType', ''),
        representation.attrib.get('mimeType', ''),
    ]
    normalized = ' '.join(values).lower()
    if 'video' in normalized:
        return 'video'
    if 'audio' in normalized:
        return 'audio'

    codecs = representation.attrib.get(
        'codecs', adaptation_set.attrib.get('codecs', '')
    ).lower()
    if codecs.startswith(VIDEO_CODECS):
        return 'video'
    if codecs.startswith(AUDIO_CODECS):
        return 'audio'
    return None


def _template_value(value, fmt):
    if value is None:
        return ''
    if fmt:
        return fmt % int(value)
    return str(value)


def _expand_template(template, representation_id=None, number=None,
                     bandwidth=None, time=None):
    if template is None:
        return None

    template = LEGACY_NUMBER_TEMPLATE_PATTERN.sub(r'$Number\1$', template)
    escaped_dollar = '\0DASH_DOLLAR\0'
    template = template.replace('$$', escaped_dollar)

    def replace(match):
        identifier = match.group('identifier')
        fmt = match.group('format')
        if identifier == 'RepresentationID':
            return _template_value(representation_id, fmt)
        if identifier == 'Number':
            return _template_value(number, fmt)
        if identifier == 'Bandwidth':
            return _template_value(bandwidth, fmt)
        if identifier == 'Time':
            return _template_value(time, fmt)
        return match.group(0)

    return DASH_TEMPLATE_PATTERN.sub(replace, template).replace(
        escaped_dollar, '$'
    )


def _timeline_repeat_count(timeline, index, current_time, duration,
                           repeat, playback_duration, timescale):
    if repeat >= 0:
        return repeat + 1

    if index + 1 < len(timeline) and 't' in timeline[index + 1]:
        next_time = int(timeline[index + 1]['t'])
        return max(0, int((next_time - current_time) / duration))

    if playback_duration:
        end_time = int(math.ceil(playback_duration * timescale))
        return max(0, int(math.ceil((end_time - current_time) /
                                    float(duration))))
    return 1


def _populate_timeline_urls(media, template, playback_duration, bandwidth,
                            representation_id):
    timeline = template.get('timeline') or []
    attributes = template['attributes']
    media_template = media.base_url
    timescale = media.timescale or 1
    segment_number = media.start or 1
    current_time = 0

    for index, item in enumerate(timeline):
        if 't' in item:
            current_time = int(item['t'])
        duration = int(item['d'])
        repeat = int(item.get('r', 0))
        repeat_count = _timeline_repeat_count(
            timeline, index, current_time, duration, repeat,
            playback_duration, timescale
        )

        if media.segment_duration is None:
            media.segment_duration = duration / timescale

        for _ in range(repeat_count):
            media.url_list.append(_expand_template(
                media_template,
                representation_id=representation_id,
                number=segment_number,
                bandwidth=bandwidth,
                time=current_time
            ))
            current_time += duration
            segment_number += 1

    if not media.segment_duration and attributes.get('duration'):
        media.segment_duration = (float(attributes['duration']) / timescale)


def _apply_segment_template(media, segment_template, base_url,
                            playback_duration, bandwidth,
                            representation_id):
    attributes = segment_template['attributes']
    media.start = int(attributes.get('startNumber', 1))
    media.timescale = float(attributes.get('timescale', 1))

    if attributes.get('duration'):
        media.segment_duration = (float(attributes['duration']) /
                                  media.timescale)

    initialization = attributes.get('initialization')
    if initialization:
        media.initialization = _expand_template(
            _join_url(base_url, initialization),
            representation_id=representation_id,
            bandwidth=bandwidth
        )

    media_path = attributes.get('media')
    if media_path:
        media.base_url = _join_url(base_url, media_path)

    if segment_template.get('timeline') and media.base_url:
        _populate_timeline_urls(
            media, segment_template, playback_duration, bandwidth,
            representation_id
        )


def _apply_segment_list(media, segment_list, base_url,
                        segment_base_initialization):
    attributes = segment_list['attributes']
    media.start = int(attributes.get('startNumber', 1))
    media.timescale = float(attributes.get('timescale', 1))

    if attributes.get('duration'):
        media.segment_duration = (float(attributes['duration']) /
                                  media.timescale)

    initialization = (segment_list.get('initialization') or
                      segment_base_initialization)
    if initialization:
        media.initialization = _join_url(base_url, initialization)

    for segment_url in segment_list['segment_urls']:
        segment_media = segment_url.get('media')
        if segment_media:
            media.url_list.append(_join_url(base_url, segment_media))
        elif segment_url.get('mediaRange') and base_url:
            media.url_list.append(base_url)


def _read_segment_sizes(representation):
    segment_sizes = []
    for segment_info in representation:
        if get_tag_name(segment_info.tag) != 'SegmentSize':
            continue
        try:
            segment_size = (float(segment_info.attrib['size']) *
                            float(SIZE_DICT[segment_info.attrib['scale']]))
        except KeyError as error:
            _log("error", "Error in reading Segment sizes :{}".format(error))
            continue
        segment_sizes.append(segment_size)
    return segment_sizes


def _fill_estimated_segment_sizes(media, bandwidth):
    if media.segment_sizes or not media.segment_duration or not media.url_list:
        return
    estimated_size = float(bandwidth) * float(media.segment_duration) / 8.0
    media.segment_sizes = [estimated_size for _ in media.url_list]


class MediaObject(object):
    """Object to handel audio and video stream """
    def __init__(self):
        self.min_buffer_time = None
        self.start = None
        self.timescale = None
        self.segment_duration = None
        self.initialization = None
        self.base_url = None
        self.url_list = list()
        self.segment_sizes = list()


class DashPlayback:
    """ 
    Audio[bandwidth] : {duration, url_list}
    Video[bandwidth] : {duration, url_list}
    """
    def __init__(self):

        self.min_buffer_time = None
        self.playback_duration = None
        self.audio = dict()
        self.video = dict()


def get_url_list(media, segment_duration,  playback_duration, bitrate):
    """
    Module to get the List of URLs
    """
    segment_duration = segment_duration or media.segment_duration
    if media.url_list:
        if segment_duration and playback_duration:
            total_segments = int(math.ceil(float(playback_duration) /
                                          float(segment_duration)))
            media.url_list = media.url_list[:total_segments]
            if media.segment_sizes:
                media.segment_sizes = media.segment_sizes[:total_segments]
        _fill_estimated_segment_sizes(media, bitrate)
        return media

    if not media.base_url or not segment_duration or not playback_duration:
        return media

    segment_count = media.start or 1
    total_segments = int(math.ceil(float(playback_duration) /
                                  float(segment_duration)))
    duration_in_timescale = int(round(float(segment_duration) *
                                      float(media.timescale or 1)))

    for offset in range(total_segments):
        media.url_list.append(_expand_template(
            media.base_url,
            representation_id=getattr(media, 'representation_id', None),
            number=segment_count + offset,
            bandwidth=bitrate,
            time=offset * duration_in_timescale
        ))

    _fill_estimated_segment_sizes(media, bitrate)
    return media


def read_mpd(mpd_file, dashplayback):
    """ Module to read the MPD file"""
    global FORMAT, URL_LIST
    FORMAT = 0
    URL_LIST = list()

    _log("info", "Reading the MPD file")
    try:
        tree = ET.parse(mpd_file)
    except IOError:
        _log("error", "MPD file not found. Exiting")
        return None

    video_metadata = config_dash.JSON_HANDLE.setdefault("video_metadata", {})
    video_metadata.clear()
    video_metadata['mpd_file'] = mpd_file
    video_metadata['available_bitrates'] = list()

    root = tree.getroot()
    if 'MPD' in get_tag_name(root.tag).upper():
        if MEDIA_PRESENTATION_DURATION in root.attrib:
            dashplayback.playback_duration = get_playback_time(root.attrib[MEDIA_PRESENTATION_DURATION])
            video_metadata['playback_duration'] = dashplayback.playback_duration
        if MIN_BUFFER_TIME in root.attrib:
            dashplayback.min_buffer_time = get_playback_time(root.attrib[MIN_BUFFER_TIME])

    video_segment_duration = None
    root_base_url = _merge_base_url(getattr(dashplayback, 'base_url', ''),
                                    root)
    periods = _children(root, 'Period')
    if not periods:
        _log("error", "No Period element found in MPD file")
        return dashplayback, video_segment_duration

    for period in periods:
        period_base_url = _merge_base_url(root_base_url, period)
        period_template = _read_segment_template(period)
        period_segment_list = _read_segment_list(period)
        period_initialization = _read_segment_base_initialization(period)

        for adaptation_set in _children(period, 'AdaptationSet'):
            adaptation_base_url = _merge_base_url(period_base_url,
                                                  adaptation_set)
            adaptation_template = _merge_segment_template(
                period_template, _read_segment_template(adaptation_set)
            )
            adaptation_segment_list = (_read_segment_list(adaptation_set) or
                                       period_segment_list)
            adaptation_initialization = (
                _read_segment_base_initialization(adaptation_set) or
                period_initialization
            )

            for representation in _children(adaptation_set, 'Representation'):
                media_type = _media_type(adaptation_set, representation)
                if media_type not in ('video', 'audio'):
                    continue

                media_object = (dashplayback.video if media_type == 'video'
                                else dashplayback.audio)
                _log("info", "Found {}".format(media_type.title()))

                try:
                    bandwidth = int(representation.attrib['bandwidth'])
                except KeyError:
                    _log("error", "Representation without bandwidth ignored")
                    continue

                representation_id = representation.attrib.get('id', bandwidth)
                representation_base_url = _merge_base_url(
                    adaptation_base_url, representation
                )
                segment_template = _merge_segment_template(
                    adaptation_template, _read_segment_template(representation)
                )
                segment_list = (_read_segment_list(representation) or
                                adaptation_segment_list)
                segment_base_initialization = (
                    _read_segment_base_initialization(representation) or
                    adaptation_initialization
                )

                media = MediaObject()
                media.representation_id = representation_id
                media.segment_sizes = _read_segment_sizes(representation)

                if segment_template:
                    _apply_segment_template(
                        media, segment_template, representation_base_url,
                        dashplayback.playback_duration, bandwidth,
                        representation_id
                    )
                elif segment_list:
                    _apply_segment_list(
                        media, segment_list, representation_base_url,
                        segment_base_initialization
                    )
                elif segment_base_initialization:
                    media.initialization = _join_url(
                        representation_base_url, segment_base_initialization
                    )

                if media_type == 'video':
                    video_metadata['available_bitrates'].append(bandwidth)
                    if video_segment_duration is None:
                        video_segment_duration = media.segment_duration
                    _log("info", "Retrieving Media")
                    _log("debug", "Segment Playback Duration = {}".format(
                        media.segment_duration
                    ))

                _fill_estimated_segment_sizes(media, bandwidth)
                media_object[bandwidth] = media

    return dashplayback, video_segment_duration
