"""Actual HTTPS decoding against an isolated local TLS video server."""

from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ssl
import threading

import cv2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import numpy as np

from app import models as m, worker
from app.jobs import claim
from test_app import post


def test_https_video_decodes_and_html_fails_preparation(client, project, runtime):
    factory, folder = runtime
    video = folder / "stream.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5, (160, 120))
    assert writer.isOpened(), "Runtime needs the MPEG-4 encoder for the HTTPS fixture"
    frame = np.random.default_rng(29).integers(30, 220, (120, 160, 3), dtype=np.uint8)
    for _ in range(15):
        writer.write(frame)
    writer.release()
    (folder / "index.html").write_text(
        "<html><body>This is not video</body></html>", encoding="utf-8"
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    instant = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(instant - timedelta(minutes=1))
        .not_valid_after(instant + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(folder))
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path, state in [("stream.mp4", "ready"), ("index.html", "failed")]:
            response = post(
                client,
                f"/projects/{project['id']}/sources",
                {"name": path, "uri": f"https://127.0.0.1:{server.server_port}/{path}"},
            )
            assert response.status_code == 200
            source_id = response.json()["source"]["id"]
            with factory() as db:
                job = claim(db)
            worker.process(job)
            with factory() as db:
                source = db.get(m.Source, source_id)
                assert source.status == state, source.error
                if state == "ready":
                    assert source.metadata_json["width"] == 160
                    assert source.metadata_json["height"] == 120
                    assert (folder / f"previews/{source_id}.jpg").is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
