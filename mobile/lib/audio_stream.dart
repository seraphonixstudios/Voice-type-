import 'dart:async';
import 'dart:typed_data';

import 'package:record/record.dart';

/// Microphone streamer: 16 kHz mono PCM16 frames for the desktop server.
/// Emits raw little-endian bytes plus per-frame RMS levels for the waveform.
///
/// iOS restriction: recording stops when the app backgrounds. Keep the app
/// in the foreground while dictating (see README).
class MicStreamer {
  MicStreamer();

  final _recorder = AudioRecorder();
  StreamSubscription<Uint8List>? _sub;
  final levels = StreamController<double>.broadcast();

  Future<bool> ensurePermission() async {
    final has = await _recorder.hasPermission();
    return has;
  }

  /// Starts streaming; calls [onFrame] with each PCM16 chunk.
  Future<void> start(void Function(Uint8List) onFrame) async {
    if (!await ensurePermission()) {
      throw StateError('microphone permission denied');
    }
    final stream = await _recorder.startStream(const RecordConfig(
      encoder: AudioEncoder.pcm16bits,
      sampleRate: 16000,
      numChannels: 1,
    ));
    _sub = stream.listen((chunk) {
      onFrame(chunk);
      levels.add(_rms(chunk));
    });
  }

  Future<void> stop() async {
    await _sub?.cancel();
    _sub = null;
    try {
      await _recorder.stop();
    } catch (_) {}
  }

  static double _rms(Uint8List chunk) {
    if (chunk.lengthInBytes < 2) return 0.0;
    final samples = chunk.lengthInBytes ~/ 2;
    var sum = 0.0;
    for (var i = 0; i < samples; i++) {
      final v = (chunk[i * 2] | (chunk[i * 2 + 1] << 8));
      final s = v >= 32768 ? v - 65536 : v;
      sum += (s / 32768.0) * (s / 32768.0);
    }
    final rms = sum / samples;
    return rms <= 0 ? 0.0 : rms.clamp(0.0, 1.0);
  }

  void dispose() {
    levels.close();
  }
}
