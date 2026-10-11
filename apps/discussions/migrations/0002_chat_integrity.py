from django.db import migrations

FORWARD = r'''
ALTER TABLE discussions_eventmessage ADD CONSTRAINT message_text_length
CHECK (char_length(text) BETWEEN 1 AND 4000 AND char_length(btrim(text, E' \t\r\n')) > 0);
ALTER TABLE discussions_eventmessage ADD CONSTRAINT message_reason_nonblank
CHECK (hidden_at IS NULL OR char_length(btrim(hidden_reason, E' \t\r\n')) > 0);

CREATE FUNCTION discussions_message_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item_kind text; archived timestamptz; closed timestamptz;
        audit_action text; audit_item text; audit_message text;
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'Messages cannot be deleted' USING ERRCODE='23514';
 END IF;
 SELECT kind, archived_at, chat_closed_at INTO item_kind, archived, closed
 FROM workitems_workitem WHERE id = NEW.work_item_id;
 IF item_kind IS DISTINCT FROM 'event' THEN
  RAISE EXCEPTION 'Chat requires an event' USING ERRCODE='23514';
 END IF;
 IF archived IS NOT NULL OR (TG_OP = 'INSERT' AND closed IS NOT NULL) THEN
  RAISE EXCEPTION 'Chat is read only' USING ERRCODE='23514';
 END IF;
 IF TG_OP = 'UPDATE' THEN
  IF NEW.work_item_id IS DISTINCT FROM OLD.work_item_id OR NEW.author_id IS DISTINCT FROM OLD.author_id
   OR NEW.text IS DISTINCT FROM OLD.text OR NEW.created_at IS DISTINCT FROM OLD.created_at
   OR OLD.hidden_at IS NOT NULL OR NEW.hidden_at IS NULL OR NEW.last_change_id <= OLD.last_change_id THEN
   RAISE EXCEPTION 'Message content and moderation history are immutable' USING ERRCODE='23514';
  END IF;
 ELSIF NEW.hidden_at IS NOT NULL THEN
  RAISE EXCEPTION 'New messages must be visible' USING ERRCODE='23514';
 END IF;
 SELECT action, object_id, changes->>'message_id' INTO audit_action, audit_item, audit_message
 FROM audit_auditentry WHERE id = NEW.last_change_id AND object_type = 'work_item';
 IF audit_item IS DISTINCT FROM NEW.work_item_id::text OR audit_message IS DISTINCT FROM NEW.id::text
  OR audit_action IS DISTINCT FROM (CASE WHEN TG_OP = 'INSERT' THEN 'discussions.sent' ELSE 'discussions.hidden' END) THEN
  RAISE EXCEPTION 'Message requires its own audit change' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER discussions_message_integrity BEFORE INSERT OR UPDATE OR DELETE ON discussions_eventmessage
FOR EACH ROW EXECUTE FUNCTION discussions_message_integrity();

CREATE FUNCTION discussions_receipt_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.operation = 'discussions.send' AND NOT EXISTS (
  SELECT 1 FROM discussions_eventmessage WHERE id = NEW.message_id
  AND work_item_id = NEW.work_item_id AND author_id = NEW.actor_id) THEN
  RAISE EXCEPTION 'Receipt does not match message' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER discussions_receipt_integrity BEFORE INSERT ON workitems_mutationreceipt
FOR EACH ROW EXECUTE FUNCTION discussions_receipt_integrity();

CREATE FUNCTION discussions_close_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.chat_closed_at IS NOT NULL AND (NEW.chat_closed_at IS DISTINCT FROM OLD.chat_closed_at OR NEW.chat_closed_by_id IS DISTINCT FROM OLD.chat_closed_by_id) THEN
  RAISE EXCEPTION 'Chat cannot be reopened' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER discussions_close_once BEFORE UPDATE ON workitems_workitem
FOR EACH ROW EXECUTE FUNCTION discussions_close_once();
'''

BACKWARD = '''
DROP TRIGGER discussions_close_once ON workitems_workitem;
DROP FUNCTION discussions_close_once();
DROP TRIGGER discussions_receipt_integrity ON workitems_mutationreceipt;
DROP FUNCTION discussions_receipt_integrity();
DROP TRIGGER discussions_message_integrity ON discussions_eventmessage;
DROP FUNCTION discussions_message_integrity();
ALTER TABLE discussions_eventmessage DROP CONSTRAINT message_text_length;
ALTER TABLE discussions_eventmessage DROP CONSTRAINT message_reason_nonblank;
'''


class Migration(migrations.Migration):
    dependencies = [('discussions', '0001_initial'), ('workitems', '0006_mutationreceipt_message_and_more')]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
