import logging
import config_dash
import sys
from time import strftime
import csv
import io
import json
import os


CSV_FIELDS = [
    'record_type',
    'metric',
    'value',
    'index',
    'segment_name',
    'bitrate',
    'size_bytes',
    'download_time_seconds',
    'download_rate_bps',
    'start_time',
    'end_time',
    'duration_seconds',
    'epoch_time',
    'playback_time',
    'buffer_size',
    'playback_state',
    'action'
]


def configure_log_file(playback_type="", log_file=None):
    """ Module to configure the log file and the log parameters.
    Logs are streamed to the log file as well as the screen.
    Log Levels: CRITICAL:50, ERROR:40, WARNING:30, INFO:20, DEBUG:10, NOTSET	0
    """
    if log_file is None:
        log_file = config_dash.LOG_FILENAME

    config_dash.LOG = logging.getLogger(config_dash.LOG_NAME)
    for handler in list(config_dash.LOG.handlers):
        config_dash.LOG.removeHandler(handler)
        handler.close()
    config_dash.LOG_LEVEL = logging.INFO
    config_dash.LOG.setLevel(config_dash.LOG_LEVEL)
    log_formatter = logging.Formatter('%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s')
    # Add the handler to print to the screen
    handler1 = logging.StreamHandler(sys.stdout)
    handler1.setFormatter(log_formatter)
    config_dash.LOG.addHandler(handler1)
    # Add the handler to for the file if present
    if log_file:
        log_filename = "_".join((log_file, playback_type, strftime('%Y-%m-%d.%H_%M_%S.log')))
        print("Configuring log file: {}".format(log_filename))
        handler2 = logging.FileHandler(filename=log_filename)
        handler2.setFormatter(log_formatter)
        config_dash.LOG.addHandler(handler2)
        print("Started logging in the log file:{}".format(log_file))


def write_json(json_data=None, json_file=None):
    """
    :param json_data: dict
    :param json_file: json file
    :return: None
        Using utf-8 to reduce size of the file
    """
    if json_data is None:
        json_data = config_dash.JSON_HANDLE
    if json_file is None:
        json_file = config_dash.JSON_LOG

    with io.open(json_file, 'w', encoding='utf-8') as json_file_handle:
        json_file_handle.write(json.dumps(json_data, ensure_ascii=False))
    write_csv(json_data)


def _csv_value(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _download_rate(segment_size, download_time):
    try:
        if float(download_time) == 0:
            return ''
        return float(segment_size) * 8 / float(download_time)
    except (TypeError, ValueError):
        return ''


def _append_metric_row(rows, record_type, metric, value):
    rows.append({
        'record_type': record_type,
        'metric': metric,
        'value': _csv_value(value)
    })


def _append_playback_rows(rows, playback_info):
    interruptions = playback_info.get('interruptions', {})
    for metric, value in playback_info.items():
        if metric == 'interruptions':
            continue
        _append_metric_row(rows, 'playback', metric, value)

    _append_metric_row(rows, 'playback', 'interruptions_count',
                       interruptions.get('count', 0))
    _append_metric_row(rows, 'playback', 'interruptions_total_duration',
                       interruptions.get('total_duration', 0))

    for index, event in enumerate(interruptions.get('events', []), 1):
        if len(event) < 2:
            continue
        start_time, end_time = event[0], event[1]
        try:
            duration = float(end_time) - float(start_time)
        except (TypeError, ValueError):
            duration = ''
        rows.append({
            'record_type': 'interruption',
            'metric': 'event',
            'index': index,
            'start_time': start_time,
            'end_time': end_time,
            'duration_seconds': duration
        })


def _append_video_metadata_rows(rows, video_metadata):
    for metric, value in video_metadata.items():
        _append_metric_row(rows, 'video_metadata', metric, value)


def _append_segment_rows(rows, segment_info):
    for index, segment in enumerate(segment_info, 1):
        segment_name = bitrate = segment_size = download_time = ''
        if isinstance(segment, dict):
            segment_name = segment.get('segment_name', segment.get('name', ''))
            bitrate = segment.get('bitrate', '')
            segment_size = segment.get('size', segment.get('size_bytes', ''))
            download_time = segment.get('download_time_seconds',
                                        segment.get('download_time', ''))
        else:
            if len(segment) > 0:
                segment_name = segment[0]
            if len(segment) > 1:
                bitrate = segment[1]
            if len(segment) > 2:
                segment_size = segment[2]
            if len(segment) > 3:
                download_time = segment[3]

        rows.append({
            'record_type': 'segment',
            'metric': 'download',
            'index': index,
            'segment_name': segment_name,
            'bitrate': bitrate,
            'size_bytes': segment_size,
            'download_time_seconds': download_time,
            'download_rate_bps': _download_rate(segment_size, download_time)
        })


def _append_buffer_rows(rows, buffer_log_file):
    if not buffer_log_file or not os.path.exists(buffer_log_file):
        return

    with open(buffer_log_file, newline='') as log_file_handle:
        reader = csv.DictReader(log_file_handle)
        for index, buffer_row in enumerate(reader, 1):
            rows.append({
                'record_type': 'buffer',
                'metric': 'state',
                'index': index,
                'epoch_time': buffer_row.get('EpochTime', ''),
                'playback_time': buffer_row.get('CurrentPlaybackTime', ''),
                'buffer_size': buffer_row.get('CurrentBufferSize', ''),
                'playback_state': buffer_row.get('CurrentPlaybackState', ''),
                'action': buffer_row.get('Action', ''),
                'bitrate': buffer_row.get('Bitrate', '')
            })


def write_csv(json_data=None, csv_file=None, buffer_log_file=None):
    """Write run metrics to a single CSV file."""
    if json_data is None:
        json_data = config_dash.JSON_HANDLE
    if csv_file is None:
        csv_file = config_dash.CSV_LOG
    if buffer_log_file is None:
        buffer_log_file = config_dash.BUFFER_LOG_FILENAME

    rows = []
    playback_type = json_data.get('playback_type')
    if playback_type is not None:
        _append_metric_row(rows, 'playback', 'playback_type', playback_type)

    _append_playback_rows(rows, json_data.get('playback_info', {}))
    _append_video_metadata_rows(rows, json_data.get('video_metadata', {}))
    _append_segment_rows(rows, json_data.get('segment_info', []))
    _append_buffer_rows(rows, buffer_log_file)

    with open(csv_file, 'w', newline='') as csv_file_handle:
        writer = csv.DictWriter(csv_file_handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
