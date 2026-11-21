ALTER TABLE agent_sessions ADD COLUMN reasoning_effort TEXT;
ALTER TABLE agent_sessions ADD COLUMN reasoning_effort_once TEXT;
ALTER TABLE standalone_builder_chats ADD COLUMN reasoning_effort TEXT;
ALTER TABLE standalone_builder_chats ADD COLUMN reasoning_effort_once TEXT;