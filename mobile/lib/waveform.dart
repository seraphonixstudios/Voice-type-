import 'package:flutter/material.dart';

/// Live waveform bars driven by per-frame RMS levels (0..1).
class Waveform extends StatefulWidget {
  const Waveform({super.key, required this.levels});

  final Stream<double> levels;

  @override
  State<Waveform> createState() => _WaveformState();
}

class _WaveformState extends State<Waveform> {
  static const _bars = 48;
  final _history = List<double>.filled(_bars, 0.0);

  @override
  void initState() {
    super.initState();
    widget.levels.listen((level) {
      if (!mounted) return;
      setState(() {
        _history.removeAt(0);
        _history.add(level.clamp(0.0, 1.0));
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _BarsPainter(List.of(_history)),
      size: const Size(double.infinity, 72),
    );
  }
}

class _BarsPainter extends CustomPainter {
  _BarsPainter(this.levels);

  final List<double> levels;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = const Color(0xFF4DA6FF);
    final n = levels.length;
    final gap = size.width / n;
    final barW = (gap * 0.6).clamp(1.0, 8.0);
    for (var i = 0; i < n; i++) {
      final h = (levels[i] * size.height).clamp(2.0, size.height);
      final x = i * gap + (gap - barW) / 2;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(x, (size.height - h) / 2, barW, h),
          const Radius.circular(2),
        ),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(_BarsPainter old) => true;
}
