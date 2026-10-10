from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunSQL(
        sql="""
        CREATE FUNCTION prevent_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Audit entries are immutable' USING ERRCODE = '55000';
        END;
        $$;
        CREATE TRIGGER audit_entry_immutable
        BEFORE UPDATE OR DELETE ON audit_auditentry
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
        """,
        reverse_sql="""
        DROP TRIGGER audit_entry_immutable ON audit_auditentry;
        DROP FUNCTION prevent_audit_mutation();
        """,
    )]
