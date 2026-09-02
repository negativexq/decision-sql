-- Local-only bootstrap. Application traffic uses this role, never the owner.
CREATE ROLE decision_reader LOGIN PASSWORD 'decision_reader_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
COMMENT ON ROLE decision_reader IS 'DecisionSQL read-only application role';
ALTER ROLE decision_reader SET default_transaction_read_only = on;
ALTER ROLE decision_reader SET statement_timeout = '5000ms';
