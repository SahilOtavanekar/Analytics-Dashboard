-- One-time request for a Snowflake admin (ACCOUNTADMIN/SECURITYADMIN) to run.
--
-- Attaches an RSA public key to your existing user for key-pair authentication.
-- This is needed for Streamlit Community Cloud, which runs headless (no browser,
-- no terminal) and can't complete the external-browser SSO flow we use locally.
--
-- This does NOT touch your SSO/browser login - it just adds a second,
-- non-interactive way to authenticate the same user, used only by the
-- deployed app. No password is set or changed.

ALTER USER "SAHIL.OTAVANEKAR@DEMANDAI.CO" SET
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAn+a5mGn5gK31z8D2S80EHoWekypXiOcNzNFjqmit+FVEdCs5h6rHTfgQ0Uj7omM3WQSb24+pmloy0U1dTo03s2uDARksxn9iWowepmXirf8hno7ytxmKOhnfLoO4uii0tYTrPB0fwKh1z7BvIateexWHc+ijgUA/13pRZV5VtuRSIKu1XjkL/9iddYORy0REC5/1i8t1+DTf4FYtIi6CeA5+LTQ/zrGSUgqET2tQu2O/G2zLWGmKmbU/fotzt/AGVVVMhcOxzzppl4Y5Byu4nqFztYYpKQW3QydAKQTeDCVwryTJosiLoB7ciGwFB7Ji4hv6I+g08Cu/G5E0ljFPTQIDAQAB';

-- Verify it attached:
-- DESCRIBE USER "SAHIL.OTAVANEKAR@DEMANDAI.CO";

-- Alternative, if you'd rather I could set this myself in the future without
-- asking again each time (optional, not required for this one-time request):
-- GRANT MODIFY PROGRAMMATIC AUTHENTICATION METHODS
--   ON USER "SAHIL.OTAVANEKAR@DEMANDAI.CO" TO ROLE R_CIT_DATA_ADMIN;
