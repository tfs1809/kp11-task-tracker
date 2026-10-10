from django.db import migrations


def populate_progress(apps, schema_editor):
    Assignment = apps.get_model('workitems', 'Assignment')
    Subtask = apps.get_model('workitems', 'Subtask')
    Progress = apps.get_model('workitems', 'SubtaskProgress')
    for assignment in Assignment.objects.all().iterator():
        Progress.objects.bulk_create([Progress(work_item_id=assignment.work_item_id, assignment_id=assignment.pk, subtask_id=pk) for pk in Subtask.objects.filter(work_item_id=assignment.work_item_id, deleted_at__isnull=True).values_list('pk', flat=True)])


FORWARD = '''
ALTER TABLE workitems_subtaskprogress ADD CONSTRAINT progress_assignment_item_fk
FOREIGN KEY (assignment_id,work_item_id) REFERENCES workitems_assignment(id,work_item_id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE workitems_subtaskprogress ADD CONSTRAINT progress_subtask_item_fk
FOREIGN KEY (subtask_id,work_item_id) REFERENCES workitems_subtask(id,work_item_id) DEFERRABLE INITIALLY IMMEDIATE;
CREATE FUNCTION workitems_subtask_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.work_item_id IS DISTINCT FROM OLD.work_item_id THEN
  RAISE EXCEPTION 'Subtask item is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER workitems_subtask_identity BEFORE UPDATE ON workitems_subtask FOR EACH ROW EXECUTE FUNCTION workitems_subtask_identity();
CREATE FUNCTION workitems_archive_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.archived_at IS NOT NULL AND (NEW.archived_at IS DISTINCT FROM OLD.archived_at OR NEW.archived_by_id IS DISTINCT FROM OLD.archived_by_id) THEN
  RAISE EXCEPTION 'Archive cannot be reversed' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER workitems_archive_once BEFORE UPDATE ON workitems_workitem FOR EACH ROW EXECUTE FUNCTION workitems_archive_once();
'''
BACKWARD = '''
DROP TRIGGER workitems_archive_once ON workitems_workitem;
DROP FUNCTION workitems_archive_once();
DROP TRIGGER workitems_subtask_identity ON workitems_subtask;
DROP FUNCTION workitems_subtask_identity();
ALTER TABLE workitems_subtaskprogress DROP CONSTRAINT progress_subtask_item_fk;
ALTER TABLE workitems_subtaskprogress DROP CONSTRAINT progress_assignment_item_fk;
'''


class Migration(migrations.Migration):
    dependencies = [('workitems', '0003_subtaskprogress')]
    # DDL перед backfill: INSERT ставит отложенные события штатных FK;
    # PostgreSQL запрещает последующий ALTER TABLE до их завершения.
    operations = [migrations.RunSQL(FORWARD, BACKWARD), migrations.RunPython(populate_progress, migrations.RunPython.noop)]
