import 'package:flutter/material.dart';

import 'audio_stream.dart';
import 'connection.dart';
import 'waveform.dart';

/// Editing/navigation command palette sent to the desktop executor.
const kPaletteCommands = [
  {'label': 'Delete word', 'action': 'delete', 'target': 'word', 'value': '1'},
  {'label': 'Delete line', 'action': 'delete', 'target': 'line', 'value': ''},
  {
    'label': 'New paragraph',
    'action': 'new_paragraph',
    'target': '',
    'value': ''
  },
  {'label': 'Scroll up', 'action': 'scroll', 'target': 'up', 'value': '3'},
  {'label': 'Scroll down', 'action': 'scroll', 'target': 'down', 'value': '3'},
  {'label': 'Undo', 'action': 'undo', 'target': '', 'value': ''},
  {'label': 'Redo', 'action': 'redo', 'target': '', 'value': ''},
  {'label': 'Copy', 'action': 'copy', 'target': '', 'value': ''},
  {'label': 'Paste', 'action': 'paste', 'target': '', 'value': ''},
  {'label': 'Select all', 'action': 'select_all', 'target': '', 'value': ''},
];

/// Home: push-to-talk with live waveform, transcription preview, palette.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.conn, required this.mic});

  final CompanionConnection conn;
  final MicStreamer mic;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  bool _holding = false;
  bool _listening = true;
  String _preview = 'Hold the button and speak.';
  int _utterances = 0;

  @override
  void initState() {
    super.initState();
    widget.conn.messages.listen((msg) {
      if (!mounted) return;
      if (msg['type'] == 'transcript') {
        setState(() {
          final text = (msg['text'] as String? ?? '').trim();
          _preview = text.isEmpty ? '(nothing heard — try again)' : text;
        });
      } else if (msg['type'] == 'listening') {
        setState(() => _listening = msg['on'] == true);
      }
    });
  }

  Future<void> _pttDown() async {
    setState(() {
      _holding = true;
      _preview = 'Listening…';
    });
    final id = 'u${DateTime.now().millisecondsSinceEpoch}';
    try {
      await widget.conn.startUtterance(id);
      await widget.mic.start(widget.conn.sendPcm16);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _holding = false;
        _preview = 'Mic error: $e';
      });
    }
  }

  Future<void> _pttUp() async {
    if (!_holding) return;
    setState(() {
      _holding = false;
      _preview = 'Transcribing…';
      _utterances++;
    });
    await widget.mic.stop();
    try {
      await widget.conn.stopUtterance();
    } catch (e) {
      if (!mounted) return;
      setState(() => _preview = 'Send failed: $e');
    }
  }

  Future<void> _runCommand(Map<String, String> cmd) async {
    try {
      await widget.conn.sendCommand(cmd['action']!,
          target: cmd['target']!, value: cmd['value']!);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Sent: ${cmd['label']}'), duration: const Duration(seconds: 1)),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Failed: $e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('VoiceType remote'),
        actions: [
          IconButton(
            tooltip: _listening ? 'Stop listening' : 'Start listening',
            icon: Icon(_listening ? Icons.mic : Icons.mic_off),
            onPressed: () async {
              try {
                await widget.conn.setListening(!_listening);
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(context)
                      .showSnackBar(SnackBar(content: Text('Failed: $e')));
                }
              }
            },
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Waveform(levels: widget.mic.levels.stream),
          const SizedBox(height: 12),
          GestureDetector(
            onTapDown: (_) => _pttDown(),
            onTapUp: (_) => _pttUp(),
            onTapCancel: _pttUp,
            child: CircleAvatar(
              radius: 56,
              backgroundColor:
                  _holding ? Colors.redAccent : const Color(0xFF4DA6FF),
              child: Icon(_holding ? Icons.fiber_manual_record : Icons.mic,
                  size: 48, color: Colors.white),
            ),
          ),
          const SizedBox(height: 8),
          const Center(
            child: Text('Hold to talk (keep the app in the foreground)',
                style: TextStyle(color: Colors.grey)),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: SelectableText(_preview,
                  style: const TextStyle(fontSize: 16)),
            ),
          ),
          const SizedBox(height: 8),
          Text('Utterances this session: $_utterances',
              style: const TextStyle(color: Colors.grey)),
          const Divider(),
          const Text('Command palette',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          for (final cmd in kPaletteCommands)
            ListTile(
              dense: true,
              title: Text(cmd['label']!),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _runCommand(cmd),
            ),
        ],
      ),
    );
  }
}
