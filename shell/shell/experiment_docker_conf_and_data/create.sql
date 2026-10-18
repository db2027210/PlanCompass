DO
$$
BEGIN
    CREATE USER monitor_user IDENTIFIED BY 'CHANGE_ME';
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE ' monitor_user ';
END
$$;

ALTER USER monitor_user SYSADMIN;
ALTER USER monitor_user LOGIN;