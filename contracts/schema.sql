-- Blueprint only: not a deployed migration. UUIDs are supplied by the application.
-- Project scoping, cross-entity consistency, RBAC and temporal rules require application validation.
-- Historical temporal v0.1 blueprint. NOT the current image-first MVP migration.
-- See docs/data-model.md and snapshot-assessment.schema.json for v0.2 design.
CREATE TABLE projects (
  id uuid PRIMARY KEY, name text NOT NULL, timezone text NOT NULL DEFAULT 'Europe/Moscow'
);
CREATE TABLE cameras (
  id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id), name text NOT NULL
);
CREATE TABLE camera_sessions (
  id uuid PRIMARY KEY, camera_id uuid NOT NULL REFERENCES cameras(id),
  started_at timestamptz NOT NULL, ended_at timestamptz,
  CHECK (ended_at IS NULL OR ended_at > started_at)
);
CREATE TABLE zones (
  id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id), name text NOT NULL
);
CREATE TABLE zone_versions (
  id uuid PRIMARY KEY, zone_id uuid NOT NULL REFERENCES zones(id),
  camera_id uuid NOT NULL REFERENCES cameras(id), version integer NOT NULL CHECK(version > 0),
  polygon jsonb NOT NULL, calibration_hash text NOT NULL,
  UNIQUE(zone_id, camera_id, version)
);
CREATE TABLE plan_versions (
  id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
  version integer NOT NULL CHECK(version > 0), source_key text NOT NULL,
  approved_at timestamptz, UNIQUE(project_id, version)
);
CREATE TABLE work_items (
  id uuid PRIMARY KEY, plan_version_id uuid NOT NULL REFERENCES plan_versions(id),
  work_key text NOT NULL, zone_id uuid NOT NULL REFERENCES zones(id),
  title text NOT NULL, starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
  resource_profile jsonb NOT NULL, CHECK(ends_at > starts_at),
  UNIQUE(plan_version_id, work_key)
);
CREATE TABLE model_versions (
  id uuid PRIMARY KEY, family text NOT NULL, weights_sha256 text NOT NULL,
  code_commit text NOT NULL, manifest_key text NOT NULL
);
CREATE TABLE processing_runs (
  id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
  model_version_id uuid NOT NULL REFERENCES model_versions(id), config_sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE media_assets (
  id uuid PRIMARY KEY, camera_session_id uuid NOT NULL REFERENCES camera_sessions(id),
  source_key text NOT NULL, object_key text NOT NULL, sha256 text NOT NULL,
  captured_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(camera_session_id, source_key)
);
CREATE INDEX media_event_time ON media_assets(camera_session_id, captured_at);
CREATE TABLE observations (
  id uuid PRIMARY KEY, media_id uuid NOT NULL REFERENCES media_assets(id),
  run_id uuid NOT NULL REFERENCES processing_runs(id),
  quality jsonb NOT NULL, detections jsonb NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now(), UNIQUE(media_id, run_id)
);
CREATE TABLE tracks (
  id uuid PRIMARY KEY, camera_session_id uuid NOT NULL REFERENCES camera_sessions(id),
  run_id uuid NOT NULL REFERENCES processing_runs(id), local_id bigint NOT NULL,
  states_manifest_key text NOT NULL, UNIQUE(camera_session_id, run_id, local_id)
);
CREATE TABLE evidence_windows (
  id uuid PRIMARY KEY, zone_version_id uuid NOT NULL REFERENCES zone_versions(id),
  run_id uuid NOT NULL REFERENCES processing_runs(id),
  equipment_class text NOT NULL,
  starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
  revision integer NOT NULL CHECK(revision > 0),
  mode text NOT NULL CHECK(mode IN ('dense_video','sparse_snapshots','insufficient_data')),
  expected_samples integer CHECK(expected_samples > 0),
  usable_samples integer NOT NULL CHECK(usable_samples >= 0),
  coverage_ratio double precision CHECK(coverage_ratio BETWEEN 0 AND 1),
  presence_sample_ratio double precision CHECK(presence_sample_ratio BETWEEN 0 AND 1),
  observed_machine_seconds double precision CHECK(observed_machine_seconds >= 0),
  manifest_key text NOT NULL, limitations jsonb NOT NULL,
  CHECK(ends_at > starts_at),
  CHECK(mode = 'dense_video' OR observed_machine_seconds IS NULL),
  CHECK(mode <> 'insufficient_data' OR presence_sample_ratio IS NULL),
  CHECK(usable_samples > 0 OR presence_sample_ratio IS NULL),
  CHECK(expected_samples IS NOT NULL OR coverage_ratio IS NULL),
  CHECK(expected_samples IS NULL OR usable_samples <= expected_samples),
  UNIQUE(zone_version_id, run_id, equipment_class, starts_at, ends_at, revision)
);
CREATE TABLE assessments (
  id uuid PRIMARY KEY, work_item_id uuid NOT NULL REFERENCES work_items(id),
  starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
  rule_version text NOT NULL, revision integer NOT NULL CHECK(revision > 0),
  status text NOT NULL CHECK(status IN ('supports_plan','potential_mismatch','insufficient_evidence')),
  explanation jsonb NOT NULL, CHECK(ends_at > starts_at),
  UNIQUE(work_item_id, starts_at, ends_at, rule_version, revision)
);
CREATE TABLE assessment_evidence (
  assessment_id uuid NOT NULL REFERENCES assessments(id),
  window_id uuid NOT NULL REFERENCES evidence_windows(id),
  PRIMARY KEY(assessment_id, window_id)
);
CREATE TABLE alerts (
  id uuid PRIMARY KEY, project_id uuid NOT NULL REFERENCES projects(id),
  assessment_id uuid NOT NULL REFERENCES assessments(id),
  fingerprint text NOT NULL, status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(project_id, fingerprint)
);
CREATE TABLE reviews (
  id uuid PRIMARY KEY, assessment_id uuid NOT NULL REFERENCES assessments(id),
  actor_id text NOT NULL, decision text NOT NULL, comment text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outbox (
  id uuid PRIMARY KEY, aggregate_id uuid NOT NULL, event_type text NOT NULL,
  payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
CREATE INDEX outbox_unpublished ON outbox(created_at) WHERE published_at IS NULL;
-- No cascaded deletes: retention and evidence tombstones need explicit policy.
