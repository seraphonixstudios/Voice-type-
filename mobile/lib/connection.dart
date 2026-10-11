import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

/// Authenticated TLS connection to the desktop companion server.
///
/// The server uses a self-signed certificate. We never trust blindly:
/// the SHA-256 fingerprint from the QR/mDNS TXT record must match the
/// presented certificate (TOFU pinning). Pairing token is single-use;
/// afterwards a per-device credential authenticates the session.
class CompanionConnection {
  CompanionConnection({required this.host, required this.port});

  final String host;
  final int port;

  WebSocket? _socket;
  String? expectedFingerprint;
  String? deviceId;
  String? _deviceSecret;
  final _incoming = StreamController<Map<String, dynamic>>.broadcast();

  Stream<Map<String, dynamic>> get messages => _incoming.stream;
  bool get isOpen => _socket != null;

  String get _scheme => 'wss';

  Future<void> connect({String? fingerprint}) async {
    if (fingerprint != null) expectedFingerprint = fingerprint;
    final client = HttpClient()
      ..badCertificateCallback = (cert, _, __) {
        final fp = sha256.convert(cert.der).toString();
        // No expected fingerprint (manual IP entry): refuse. The user must
        // verify the fingerprint shown on the desktop first run screen.
        if (expectedFingerprint == null) return false;
        return fp.toLowerCase() ==
            expectedFingerprint!.toLowerCase().replaceAll(':', '');
      };
    final uri = Uri.parse('$_scheme://$host:$port');
    _socket = await WebSocket.connect(uri.toString(), customClient: client);
    _socket!.listen(_route, onDone: close, onError: (_) => close());
  }

  void _route(dynamic data) {
    if (data is String) {
      try {
        final msg = jsonDecode(data);
        if (msg is Map<String, dynamic>) _incoming.add(msg);
      } catch (_) {
        // ignore malformed frames; server logs them
      }
    }
  }

  Future<Map<String, dynamic>> _request(Map<String, dynamic> msg) async {
    final socket = _socket;
    if (socket == null) throw StateError('not connected');
    final completer = Completer<Map<String, dynamic>>();
    late StreamSubscription sub;
    sub = messages.listen((m) {
      if (!completer.isCompleted) {
        completer.complete(m);
        sub.cancel();
      }
    });
    socket.add(jsonEncode(msg));
    return completer.future.timeout(const Duration(seconds: 15), onTimeout: () {
      sub.cancel();
      throw TimeoutException('no reply from desktop');
    });
  }

  /// First-run pairing. Returns (deviceId, deviceSecret) to store on-device.
  Future<void> pair(String token) async {
    final reply = await _request({'type': 'pair', 'token': token});
    if (reply['type'] != 'paired') {
      throw StateError(reply['message'] as String? ?? 'pairing rejected');
    }
    deviceId = reply['device_id'] as String;
    _deviceSecret = reply['device_token'] as String;
  }

  Future<void> auth(String id, String secret) async {
    deviceId = id;
    _deviceSecret = secret;
    final reply = await _request(
        {'type': 'auth', 'device_id': id, 'secret': secret});
    if (reply['type'] != 'authed') {
      throw StateError(reply['message'] as String? ?? 'auth rejected');
    }
  }

  Future<void> sendCommand(String action,
      {String target = '', String value = ''}) async {
    await _request(
        {'type': 'command', 'action': action, 'target': target, 'value': value});
  }

  Future<void> setListening(bool on) async {
    await _request({'type': 'set_listening', 'on': on});
  }

  Future<void> ping() async => _request({'type': 'ping'});

  void sendPcm16(Uint8List bytes) => _socket?.add(bytes);

  Future<Map<String, dynamic>> startUtterance(String id) =>
      _request({'type': 'ptt_start', 'id': id});

  Future<void> stopUtterance() async {
    await _request({'type': 'ptt_stop'});
  }

  Future<void> close() async {
    await _incoming.close().catchError((_) {});
    await _socket?.close().catchError((_) {});
    _socket = null;
  }
}
