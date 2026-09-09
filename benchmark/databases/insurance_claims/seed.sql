INSERT INTO policyholders VALUES (1,'Aster Group','North'),(2,'Boreal Works','South'),(3,'Cinder Labs','East');
INSERT INTO policies VALUES (101,1,'auto','active','2026-01-01','2026-12-31'),(102,1,'home','active','2026-02-01','2027-01-31'),(103,2,'travel','expired','2025-01-01','2025-12-31'),(104,3,'auto','active','2026-03-01','2027-02-28');
INSERT INTO claims VALUES (201,101,'2026-06-01',1200,'open','{"severity":"4"}'),(202,101,'2026-05-01',300,'closed','{"severity":"2"}'),(203,102,'2026-06-05',800,'open','{"severity":"5"}'),(204,103,'2026-04-01',500,'closed','{"severity":"3"}'),(205,104,'2026-06-10',100,'open','{"severity":"1"}');
INSERT INTO claim_payments VALUES (301,201,'2026-06-10',500,'posted'),(302,202,'2026-05-10',300,'posted'),(303,203,'2026-06-15',100,'pending');
INSERT INTO claim_events VALUES (401,201,'2026-06-01 10:00+00','opened'),(402,201,'2026-06-03 10:00+00','reviewed'),(403,202,'2026-05-01 10:00+00','opened'),(404,203,'2026-06-05 10:00+00','opened'),(405,205,'2026-06-10 10:00+00','opened');
INSERT INTO adjusters VALUES (501,'Riley'),(502,'Morgan');
INSERT INTO claim_assignments VALUES (601,201,501,'2026-06-02 10:00+00'),(602,203,502,'2026-06-06 10:00+00');
INSERT INTO external_directory VALUES (701,1,'beneficiary@example.invalid');
