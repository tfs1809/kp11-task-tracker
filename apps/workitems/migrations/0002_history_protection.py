from django.db import migrations

FORWARD = '''
CREATE FUNCTION workitems_immutable_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.author_id IS DISTINCT FROM OLD.author_id OR NEW.kind IS DISTINCT FROM OLD.kind THEN
    RAISE EXCEPTION 'Work item author and kind are immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER workitems_identity BEFORE UPDATE ON workitems_workitem
FOR EACH ROW EXECUTE FUNCTION workitems_immutable_identity();

CREATE FUNCTION workitems_immutable_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Issuance history is immutable' USING ERRCODE = '23514';
END $$;

CREATE FUNCTION workitems_source_consistency() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE selection_uuid uuid; selected_item uuid; selected_batch uuid; assigned_item uuid; assigned_user uuid; selected_user uuid;
BEGIN
  SELECT work_item_id, user_id INTO assigned_item, assigned_user FROM workitems_assignment WHERE id = NEW.assignment_id;
  IF NEW.selected_user_id IS NOT NULL THEN
    SELECT selection_id, user_id INTO selection_uuid, selected_user FROM workitems_audienceselectionuser WHERE id = NEW.selected_user_id;
    IF selected_user IS DISTINCT FROM assigned_user THEN
      RAISE EXCEPTION 'Direct recipient does not match assignment' USING ERRCODE = '23514';
    END IF;
  ELSIF NEW.selected_role_id IS NOT NULL THEN
    SELECT selection_id INTO selection_uuid FROM workitems_audienceselectionrole WHERE id = NEW.selected_role_id;
  ELSE
    SELECT selection_id INTO selection_uuid FROM workitems_audienceselectiongroup WHERE id = NEW.selected_group_id;
  END IF;
  SELECT work_item_id, batch_id INTO selected_item, selected_batch FROM workitems_audienceselection WHERE id = selection_uuid;
  IF assigned_item IS DISTINCT FROM selected_item OR NEW.batch_id IS DISTINCT FROM selected_batch THEN
    RAISE EXCEPTION 'Assignment source belongs to another item or batch' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER workitems_source_valid BEFORE INSERT ON workitems_assignmentsource
FOR EACH ROW EXECUTE FUNCTION workitems_source_consistency();

CREATE FUNCTION workitems_assignment_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.work_item_id IS DISTINCT FROM OLD.work_item_id OR NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.first_assigned_at IS DISTINCT FROM OLD.first_assigned_at THEN
    RAISE EXCEPTION 'Assignment identity is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER workitems_assignment_identity BEFORE UPDATE ON workitems_assignment
FOR EACH ROW EXECUTE FUNCTION workitems_assignment_identity();
'''
HISTORY_TABLES = ['audienceselection', 'audienceselectionuser', 'audienceselectionrole', 'audienceselectiongroup', 'assignmentsource', 'mutationreceipt']
FORWARD += '\n'.join(f'CREATE TRIGGER workitems_{name}_immutable BEFORE UPDATE OR DELETE ON workitems_{name} FOR EACH ROW EXECUTE FUNCTION workitems_immutable_history();' for name in HISTORY_TABLES)
BACKWARD = '\n'.join(f'DROP TRIGGER workitems_{name}_immutable ON workitems_{name};' for name in HISTORY_TABLES) + '''
DROP TRIGGER workitems_assignment_identity ON workitems_assignment;
DROP FUNCTION workitems_assignment_identity();
DROP TRIGGER workitems_source_valid ON workitems_assignmentsource;
DROP FUNCTION workitems_source_consistency();
DROP FUNCTION workitems_immutable_history();
DROP TRIGGER workitems_identity ON workitems_workitem;
DROP FUNCTION workitems_immutable_identity();
'''


class Migration(migrations.Migration):
    dependencies = [('workitems', '0001_initial')]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
