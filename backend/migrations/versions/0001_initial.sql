CREATE EXTENSION IF NOT EXISTS vector;


CREATE TABLE records (
	id VARCHAR(36) NOT NULL,
	client_key VARCHAR(80),
	kind VARCHAR(20) NOT NULL,
	title VARCHAR(300) NOT NULL,
	content TEXT NOT NULL,
	url TEXT,
	url_key VARCHAR(64),
	notes TEXT NOT NULL,
	purpose VARCHAR(20) NOT NULL,
	version INTEGER NOT NULL,
	status VARCHAR(30) NOT NULL,
	coverage JSON NOT NULL,
	archived BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (client_key),
	UNIQUE (url_key)
)

;

CREATE INDEX ix_records_status ON records (status);


CREATE TABLE wiki_pages (
	id VARCHAR(36) NOT NULL,
	title VARCHAR(200) NOT NULL,
	title_key VARCHAR(200) NOT NULL,
	generated TEXT NOT NULL,
	personal TEXT NOT NULL,
	version INTEGER NOT NULL,
	source_ids JSON NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (title_key)
)

;


CREATE TABLE attachments (
	id VARCHAR(36) NOT NULL,
	record_id VARCHAR(36) NOT NULL,
	filename VARCHAR(255) NOT NULL,
	storage_key VARCHAR(300) NOT NULL,
	media_type VARCHAR(100) NOT NULL,
	size INTEGER NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(record_id) REFERENCES records (id),
	UNIQUE (storage_key)
)

;

CREATE INDEX ix_attachments_record_id ON attachments (record_id);


CREATE TABLE processing_runs (
	id VARCHAR(36) NOT NULL,
	record_id VARCHAR(36) NOT NULL,
	version INTEGER NOT NULL,
	status VARCHAR(30) NOT NULL,
	stage VARCHAR(30) NOT NULL,
	attempts INTEGER NOT NULL,
	error TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(record_id) REFERENCES records (id)
)

;

CREATE INDEX ix_processing_runs_record_id ON processing_runs (record_id);

CREATE INDEX ix_processing_runs_status ON processing_runs (status);


CREATE TABLE relations (
	id VARCHAR(36) NOT NULL,
	record_id VARCHAR(36) NOT NULL,
	target_id VARCHAR(36) NOT NULL,
	kind VARCHAR(30) NOT NULL,
	explanation TEXT NOT NULL,
	evidence JSON NOT NULL,
	feedback VARCHAR(20) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (record_id, target_id, kind),
	FOREIGN KEY(record_id) REFERENCES records (id),
	FOREIGN KEY(target_id) REFERENCES records (id)
)

;

CREATE INDEX ix_relations_record_id ON relations (record_id);

CREATE INDEX ix_relations_target_id ON relations (target_id);


CREATE TABLE sources (
	id VARCHAR(36) NOT NULL,
	record_id VARCHAR(36) NOT NULL,
	version INTEGER NOT NULL,
	origin VARCHAR(100) NOT NULL,
	ordinal INTEGER NOT NULL,
	text TEXT NOT NULL,
	locator JSON NOT NULL,
	method VARCHAR(50) NOT NULL,
	search_text TEXT NOT NULL,
	embedding VECTOR(1024),
	embedding_model VARCHAR(200),
	PRIMARY KEY (id),
	UNIQUE (record_id, version, origin, ordinal),
	FOREIGN KEY(record_id) REFERENCES records (id)
)

;

CREATE INDEX ix_sources_record_id ON sources (record_id);


CREATE TABLE analyses (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	record_id VARCHAR(36) NOT NULL,
	version INTEGER NOT NULL,
	kind VARCHAR(30) NOT NULL,
	summary TEXT NOT NULL,
	topics JSON NOT NULL,
	points JSON NOT NULL,
	observations TEXT NOT NULL,
	model VARCHAR(200) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (run_id),
	FOREIGN KEY(run_id) REFERENCES processing_runs (id),
	FOREIGN KEY(record_id) REFERENCES records (id)
)

;

CREATE INDEX ix_analyses_record_id ON analyses (record_id);


CREATE TABLE model_calls (
	id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36) NOT NULL,
	stage VARCHAR(50) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	model VARCHAR(200) NOT NULL,
	status VARCHAR(30) NOT NULL,
	charged_tokens INTEGER NOT NULL,
	input_tokens INTEGER,
	output_tokens INTEGER,
	response JSON,
	duration_seconds FLOAT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(run_id) REFERENCES processing_runs (id)
)

;

CREATE INDEX ix_call_cache ON model_calls (run_id, request_hash, status);

CREATE INDEX ix_model_calls_created_at ON model_calls (created_at);

CREATE INDEX ix_model_calls_run_id ON model_calls (run_id);


CREATE TABLE wiki_revisions (
	id VARCHAR(36) NOT NULL,
	page_id VARCHAR(36) NOT NULL,
	run_id VARCHAR(36),
	version INTEGER NOT NULL,
	generated TEXT NOT NULL,
	personal TEXT NOT NULL,
	source_ids JSON NOT NULL,
	actor VARCHAR(20) NOT NULL,
	change_note TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (page_id, version),
	UNIQUE (page_id, run_id),
	FOREIGN KEY(page_id) REFERENCES wiki_pages (id),
	FOREIGN KEY(run_id) REFERENCES processing_runs (id)
)

;

CREATE INDEX ix_wiki_revisions_page_id ON wiki_revisions (page_id);

CREATE INDEX ix_sources_fulltext ON sources USING gin (to_tsvector('simple', search_text));

CREATE INDEX ix_sources_embedding ON sources USING hnsw (embedding vector_cosine_ops);
