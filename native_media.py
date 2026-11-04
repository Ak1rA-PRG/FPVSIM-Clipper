"""Native OS trimming. No FFmpeg binaries, libraries, or subprocesses."""
import asyncio
from datetime import timedelta
from pathlib import Path
import platform
import threading
import math

_render_lock = threading.Lock()


def validate_native_media():
    if platform.system() == "Windows":
        from winrt.runtime import init_apartment, uninit_apartment, ApartmentType
        from winrt.windows.media.editing import MediaComposition
        init_apartment(ApartmentType.MULTI_THREADED)
        try:
            composition = MediaComposition()
            del composition
        finally:
            uninit_apartment()
    elif platform.system() == "Darwin":
        import AVFoundation
        if AVFoundation.AVAssetExportSession is None:
            raise RuntimeError("AVFoundationが利用できません")
    else:
        raise RuntimeError("動画の切り出しはWindows / macOSに対応しています")


def interval_start(total, duration, end_offset):
    if not all(math.isfinite(value) for value in (total, duration, end_offset)) or duration <= 0 or end_offset < 0:
        raise ValueError("切り出し時間が不正です")
    if total + 0.02 < duration + end_offset:
        raise RuntimeError(
            f"元動画が短いため切り出せません（必要 {duration + end_offset:.3f}秒 / 元動画 {total:.3f}秒）。"
            "OBSの最大リプレイ時間を増やしてください。元動画は保持します"
        )
    return max(0.0, total - duration - end_offset)


async def _windows_render(source, target, duration, end_offset, precise):
    # Import generic async/collection projections before invoking the WinRT methods.
    import winrt.windows.foundation
    import winrt.windows.foundation.collections
    import winrt.windows.storage.streams
    import winrt.windows.media.mediaproperties
    from winrt.windows.storage import StorageFile, StorageFolder, CreationCollisionOption
    from winrt.windows.media.editing import MediaClip, MediaComposition, MediaTrimmingPreference
    from winrt.windows.media.transcoding import TranscodeFailureReason
    source_file = await StorageFile.get_file_from_path_async(str(source.resolve()))
    clip = await MediaClip.create_from_file_async(source_file)
    total = clip.original_duration.total_seconds()
    start = interval_start(total, duration, end_offset)
    clip.trim_time_from_start = timedelta(seconds=start)
    clip.trim_time_from_end = timedelta(seconds=end_offset)
    composition = MediaComposition()
    composition.clips.append(clip)
    profile = composition.create_default_encoding_profile()
    video = clip.get_video_encoding_properties()
    expected = clip.trimmed_duration.total_seconds()
    folder = await StorageFolder.get_folder_from_path_async(str(target.parent.resolve()))
    output = await folder.create_file_async(target.name, CreationCollisionOption.FAIL_IF_EXISTS)
    # Keep the recorded video's output dimensions and frame rate.
    profile.video.width = video.width
    profile.video.height = video.height
    profile.video.frame_rate.numerator = video.frame_rate.numerator
    profile.video.frame_rate.denominator = video.frame_rate.denominator
    # Use the native input dimensions/frame rate. The MP4 profile uses native H.264/AAC.
    result = await composition.render_to_file_with_profile_async(
        output, MediaTrimmingPreference.PRECISE if precise else MediaTrimmingPreference.FAST, profile,
    )
    if result != TranscodeFailureReason.NONE:
        raise RuntimeError(f"Windows標準の動画切り出しに失敗しました: {result}")
    # Validate the actual exported duration without a third-party probe executable.
    exported = await MediaClip.create_from_file_async(output)
    actual = exported.original_duration.total_seconds()
    if precise and abs(actual - expected) > 0.15:
        raise RuntimeError(f"出力の長さを確認できません（指定 {expected:.3f}秒 / 出力 {actual:.3f}秒）")
    if not math.isfinite(actual) or actual <= 0 or actual > expected + 0.15:
        raise RuntimeError("出力動画の長さが不正です")




def _mac_render(source, target, duration, end_offset, precise):
    import AVFoundation as AV
    import CoreMedia as CM
    from Foundation import NSURL
    asset = AV.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(str(source.resolve())), None)
    total = CM.CMTimeGetSeconds(asset.duration())
    start = interval_start(total, duration, end_offset)
    expected = duration
    preset = AV.AVAssetExportPresetHighestQuality if precise else AV.AVAssetExportPresetPassthrough
    session = AV.AVAssetExportSession.alloc().initWithAsset_presetName_(asset, preset)
    if session is None:
        raise RuntimeError("AVFoundationがこの動画を読み込めません")
    session.setOutputURL_(NSURL.fileURLWithPath_(str(target.resolve())))
    session.setOutputFileType_(AV.AVFileTypeMPEG4)
    session.setTimeRange_(CM.CMTimeRangeMake(CM.CMTimeMakeWithSeconds(start, 60000), CM.CMTimeMakeWithSeconds(expected, 60000)))
    done = threading.Event()
    session.exportAsynchronouslyWithCompletionHandler_(done.set)
    if not done.wait(1800):
        session.cancelExport()
        raise RuntimeError("動画の切り出しがタイムアウトしました。元動画は保持します")
    if session.status() != AV.AVAssetExportSessionStatusCompleted:
        raise RuntimeError(f"AVFoundationの切り出しに失敗しました: {session.error()}")
    exported = AV.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(str(target.resolve())), None)
    actual = CM.CMTimeGetSeconds(exported.duration())
    if precise and abs(actual - expected) > 0.15:
        raise RuntimeError(f"出力の長さを確認できません（指定 {expected:.3f}秒 / 出力 {actual:.3f}秒）")
    if not math.isfinite(actual) or actual <= 0 or actual > expected + 0.15:
        raise RuntimeError("出力動画の長さが不正です")


def render_interval(source: Path, target: Path, duration: float, end_offset: float, precise: bool = True):
    with _render_lock:
        if platform.system() == "Windows":
            from winrt.runtime import init_apartment, uninit_apartment, ApartmentType
            init_apartment(ApartmentType.MULTI_THREADED)
            try:
                asyncio.run(asyncio.wait_for(_windows_render(source, target, duration, end_offset, precise), 1800))
            finally:
                uninit_apartment()
        elif platform.system() == "Darwin":
            _mac_render(source, target, duration, end_offset, precise)
        else:
            raise RuntimeError("対応していないOSです")
