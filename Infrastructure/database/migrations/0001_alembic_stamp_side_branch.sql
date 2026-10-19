-- migration: downtime=false
-- Production applied the 009 -> 03fbbb9b5ba3 -> 04fbbb9b5ba4 -> 010 alembic
-- branch by hand but alembic_version only records 05_relay_observation (the
-- other head). Running `alembic upgrade heads` there would re-run that branch,
-- including 009_seed_device_registry, against live data. Record the branch head
-- when, and only when, its effects are verifiably present. Runs before alembic
-- (filename contains alembic_stamp); 011_merge_heads then joins the two heads.
BEGIN;

DO $$
DECLARE
    versions TEXT[];
    non_canonical BIGINT;
BEGIN
    IF to_regclass('public.alembic_version') IS NULL THEN
        RETURN;  -- fresh install: alembic builds the schema itself
    END IF;

    SELECT coalesce(array_agg(version_num ORDER BY version_num), ARRAY[]::TEXT[])
      INTO versions
      FROM public.alembic_version;

    IF versions && ARRAY['010_canonicalize_device_names', '011_merge_heads'] THEN
        RETURN;  -- already stamped or merged
    END IF;

    IF versions <> ARRAY['05_relay_observation'] THEN
        RETURN;  -- linear history: alembic upgrades normally
    END IF;

    SELECT count(*) INTO non_canonical
      FROM public.device_registry
     WHERE device_name !~ '^[a-z][a-z0-9]*_[fvlo]_\d+$';

    IF to_regclass('public.light_target_intensity') IS NULL
       OR to_regclass('public.light_programs') IS NULL
       OR non_canonical > 0 THEN
        RAISE EXCEPTION 'alembic_side_branch_effects_missing'
            USING DETAIL = format(
                'light_target_intensity=%s light_programs=%s non_canonical_device_names=%s',
                to_regclass('public.light_target_intensity') IS NOT NULL,
                to_regclass('public.light_programs') IS NOT NULL,
                non_canonical);
    END IF;

    INSERT INTO public.alembic_version (version_num)
    VALUES ('010_canonicalize_device_names');
END
$$;

COMMIT;
