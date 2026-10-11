import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:multicast_dns/multicast_dns.dart';

import 'connection.dart';

/// Pairing: automatic mDNS discovery, QR scan, or manual entry.
/// No cloud, no account — everything resolves on the local network.
class PairingScreen extends StatefulWidget {
  const PairingScreen({super.key, required this.onPaired});

  final void Function(CompanionConnection conn) onPaired;

  @override
  State<PairingScreen> createState() => _PairingScreenState();
}

class _FoundHost {
  _FoundHost(this.host, this.port, this.fingerprint);
  final String host;
  final int port;
  final String fingerprint;
}

class _PairingScreenState extends State<PairingScreen> {
  final _manualHost = TextEditingController();
  final _manualToken = TextEditingController();
  List<_FoundHost> _found = [];
  bool _scanning = false;
  String? _status;

  @override
  void dispose() {
    _manualHost.dispose();
    _manualToken.dispose();
    super.dispose();
  }

  Future<void> _discover() async {
    setState(() {
      _scanning = true;
      _status = 'Searching LAN for VoiceType desktops…';
    });
    final found = <_FoundHost>[];
    try {
      final client = MDnsClient();
      await client.start();
      await for (final ptr in client.lookup<PtrResourceRecord>(
          ResourceRecordQuery.serverPointer('_voicetype._tcp.local.'),
          timeout: const Duration(seconds: 4))) {
        try {
          final srv = await client
              .lookup<SrvResourceRecord>(
                  ResourceRecordQuery.service(ptr.domainName))
              .first
              .timeout(const Duration(seconds: 2));
          String fp = '';
          await for (final txt in client.lookup<TxtResourceRecord>(
              ResourceRecordQuery.text(ptr.domainName),
              timeout: const Duration(seconds: 2))) {
            final text = txt.text;
            final m = RegExp(r'fp=([0-9a-fA-F]+)').firstMatch(text);
            if (m != null) fp = m.group(1)!;
            break;
          }
          found.add(_FoundHost(
              srv.target.replaceAll(RegExp(r'\.$'), ''), srv.port, fp));
        } catch (_) {}
      }
      client.stop();
    } catch (e) {
      setState(() => _status = 'Discovery failed ($e). Scan the QR code.');
    }
    if (!mounted) return;
    setState(() {
      _found = found;
      _scanning = false;
      _status = found.isEmpty
          ? 'No desktop found. Make sure it runs with --companion on the same Wi-Fi, then scan its QR.'
          : 'Found ${found.length} desktop(s). Pick one, then scan its QR for the token.';
    });
  }

  Future<void> _pairWith({
    required String host,
    required int port,
    required String token,
    String fingerprint = '',
  }) async {
    setState(() => _status = 'Pairing with $host…');
    final conn = CompanionConnection(host: host, port: port);
    try {
      await conn.connect(
          fingerprint: fingerprint.isEmpty ? null : fingerprint);
      await conn.pair(token);
      if (!mounted) return;
      widget.onPaired(conn);
    } catch (e) {
      await conn.close();
      if (!mounted) return;
      setState(() => _status = 'Pairing failed: $e');
    }
  }

  void _openScanner() {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => Scaffold(
        appBar: AppBar(title: const Text('Scan desktop QR')),
        body: MobileScanner(onDetect: (capture) {
          final raw = capture.barcodes.firstOrNull?.rawValue;
          if (raw == null || !raw.startsWith('voicetype://pair?')) return;
          Navigator.of(context).pop();
          final uri = Uri.parse(raw);
          _pairWith(
            host: uri.queryParameters['host'] ?? '',
            port: int.tryParse(uri.queryParameters['port'] ?? '') ?? 8765,
            token: uri.queryParameters['token'] ?? '',
            fingerprint: uri.queryParameters['fp'] ?? '',
          );
        }),
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Pair with desktop')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          FilledButton.icon(
            onPressed: _scanning ? null : _discover,
            icon: const Icon(Icons.radar),
            label: const Text('Find desktops on LAN'),
          ),
          const SizedBox(height: 8),
          FilledButton.tonalIcon(
            onPressed: _openScanner,
            icon: const Icon(Icons.qr_code_scanner),
            label: const Text('Scan pairing QR'),
          ),
          if (_status != null) ...[
            const SizedBox(height: 12),
            Text(_status!),
          ],
          for (final h in _found)
            ListTile(
              leading: const Icon(Icons.computer),
              title: Text('${h.host}:${h.port}'),
              subtitle: const Text('Tap, then scan its QR for the token'),
              onTap: () {
                _manualHost.text = h.host;
              },
            ),
          const Divider(),
          const Text('Manual entry (token from `voicetype --show-qr`)'),
          TextField(
            controller: _manualHost,
            decoration: const InputDecoration(labelText: 'Desktop host or IP'),
          ),
          TextField(
            controller: _manualToken,
            decoration: const InputDecoration(labelText: 'Pairing token'),
          ),
          const SizedBox(height: 8),
          OutlinedButton(
            onPressed: () => _pairWith(
              host: _manualHost.text.trim(),
              port: 8765,
              token: _manualToken.text.trim(),
            ),
            child: const Text('Pair manually'),
          ),
        ],
      ),
    );
  }
}
