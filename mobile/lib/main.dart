import 'package:flutter/material.dart';

import 'audio_stream.dart';
import 'connection.dart';
import 'home_screen.dart';
import 'pairing_screen.dart';

void main() => runApp(const VoiceTypeCompanion());

class VoiceTypeCompanion extends StatelessWidget {
  const VoiceTypeCompanion({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'VoiceType',
      theme: ThemeData.dark(useMaterial3: true).copyWith(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF4DA6FF),
          brightness: Brightness.dark,
        ),
      ),
      home: const _Gate(),
    );
  }
}

class _Gate extends StatefulWidget {
  const _Gate();

  @override
  State<_Gate> createState() => _GateState();
}

class _GateState extends State<_Gate> {
  CompanionConnection? _conn;
  final _mic = MicStreamer();

  @override
  void dispose() {
    _mic.dispose();
    _conn?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final conn = _conn;
    if (conn == null) {
      return PairingScreen(onPaired: (c) => setState(() => _conn = c));
    }
    return HomeScreen(conn: conn, mic: _mic);
  }
}
