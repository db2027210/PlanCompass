package com.hybench.workload;
/**
 *
 * @time 2023-03-04
 * @version 1.0.0
 * @file APClient.java
 * @description
 *   AP Client Processor
 **/


import com.hybench.dbconn.ConnectionMgr;
import com.hybench.util.RandomGenerator;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.sql.*;
import java.util.ArrayList;

public class APClient extends Client{
    int customer_no;
    int company_no;
//    RandomGenerator rg = new RandomGenerator();

    @Override
    // init the db here
    public void doInit(){

        // tentative parameter curation
        Long customer_number = CR.customer_number;
        Long company_number= CR.company_number;
        customer_no = customer_number.intValue() + 1;
        company_no = company_number.intValue();

    }

    public int Get_blocked_transfer_Id(){
        int Id=0;
        Id=Related_Blocked_Transfer_ids.get(rg.getRandomint(Related_Blocked_Transfer_ids.size()));
        return Id;
    }

    public int Get_blocked_account_Id(){
        if(queue_ids.size()==0)
            return rg.getRandomint(customer_no, customer_no+company_no);
        else{
            int Id=0;
            int index = rg.getRandomint(queue_ids.size());
            int i = 0;
            for(Integer obj : queue_ids)
            {
                if (i == index)
                    Id=obj;
                i++;
            }
            return Id;
        }
    }

    private int[] parseApIntList(String propertyName) {
        String raw = strParameter(propertyName, "").trim();
        if (raw.isEmpty()) {
            return null;
        }
        String[] parts = raw.split(",");
        int[] values = new int[parts.length];
        for (int i = 0; i < parts.length; i++) {
            values[i] = Integer.parseInt(parts[i].trim());
        }
        return values;
    }

    private int[] configuredApQueryIds() {
        int[] queryIds = parseApIntList("ap_query_ids");
        if (queryIds == null || queryIds.length == 0) {
            return null;
        }
        for (int queryId : queryIds) {
            if (queryId < 1 || queryId > 13) {
                throw new IllegalArgumentException("ap_query_ids must contain query ids in [1,13], got " + queryId);
            }
        }
        return queryIds;
    }

    private int[] configuredApQuerySequence() {
        int[] queryIds = parseApIntList("ap_query_sequence");
        if (queryIds == null || queryIds.length == 0) {
            return null;
        }
        for (int queryId : queryIds) {
            if (queryId < 1 || queryId > 13) {
                throw new IllegalArgumentException("ap_query_sequence must contain query ids in [1,13], got " + queryId);
            }
        }
        return queryIds;
    }

    private int[] configuredApQueryWeights(int expectedLength) {
        int[] weights = parseApIntList("ap_query_weights");
        if (weights == null) {
            weights = new int[expectedLength];
            for (int i = 0; i < weights.length; i++) {
                weights[i] = 1;
            }
            return weights;
        }
        if (weights.length != expectedLength) {
            throw new IllegalArgumentException("ap_query_weights length must equal ap_query_ids length");
        }
        for (int weight : weights) {
            if (weight <= 0) {
                throw new IllegalArgumentException("ap_query_weights must be positive, got " + weight);
            }
        }
        return weights;
    }

    private int chooseWeightedApQuery(int[] queryIds, int[] weights) {
        int totalWeight = 0;
        for (int weight : weights) {
            totalWeight += weight;
        }
        int draw = rg.getRandomint(totalWeight);
        int cumulative = 0;
        for (int i = 0; i < queryIds.length; i++) {
            cumulative += weights[i];
            if (draw < cumulative) {
                return queryIds[i];
            }
        }
        return queryIds[queryIds.length - 1];
    }

    private String formatApWeightedConfig(int[] queryIds, int[] weights) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < queryIds.length; i++) {
            if (i > 0) {
                sb.append(", ");
            }
            sb.append("Q").append(queryIds[i]).append(":").append(weights[i]);
        }
        return sb.toString();
    }

    private String formatApQuerySequence(int[] queryIds) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < queryIds.length; i++) {
            if (i > 0) {
                sb.append(",");
            }
            sb.append("Q").append(queryIds[i]);
        }
        return sb.toString();
    }


    // 6 Interative Queries
    public ClientResult execIQ1(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int custid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_iq1());

            double rate= rg.getRandomDouble();
            if(rate<risk_rate/2)
                custid = Get_blocked_transfer_Id();
            else if (rate<risk_rate){
                custid = Get_blocked_account_Id();
            }
            else
                custid=rg.getRandomint(customer_no);

            pstmt.setInt(1,custid);
            pstmt.setInt(2,custid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getXPIQItem(0).addValue(responseTime);
            lock.lock();
            iqTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execIQ2(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int companyid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_iq2());
          //  companyid = rg.getRandomint(customer_no, customer_no+company_no);
            double rate= rg.getRandomDouble();
            if(rate<risk_rate)
                companyid = Get_blocked_account_Id();
            else
                companyid = rg.getRandomint(customer_no, customer_no+company_no);


            pstmt.setInt(1,companyid);
            pstmt.setInt(2,companyid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getXPIQItem(1).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execIQ3(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int companyid=0;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_iq3());
            companyid = rg.getRandomint(customer_no,customer_no+company_no);
            pstmt.setInt(1,companyid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getXPIQItem(2).addValue(responseTime);
            lock.lock();
            iqTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        } finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execIQ4(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int companyid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_iq4());
            companyid = rg.getRandomint(customer_no,customer_no+company_no);
            pstmt.setInt(1,companyid);
            pstmt.setInt(2,companyid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            hist.getXPIQItem(3).addValue(responseTime);
            logger.debug(pstmt.toString());
            lock.lock();
            iqTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execIQ5(Connection conn){

        ClientResult cr = new ClientResult();
        Statement stmt = null;
        int applicantid=1;
        try{
            stmt = conn.createStatement();;
            ResultSet rs = stmt.executeQuery(sqls.ap_iq5_1());
            while ( rs.next() ) {
                applicantid= rs.getInt(1);
            }
            rs.close();
        } catch (SQLException throwables) {
            throwables.printStackTrace();
        }finally {

            try {
                stmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }

        PreparedStatement pstmt = null;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_iq5());
            pstmt.setInt(1,applicantid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            hist.getXPIQItem(4).addValue(responseTime);
            logger.debug(pstmt.toString());
            lock.lock();
            iqTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execIQ6(Connection conn){

        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_iq6());
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            hist.getXPIQItem(5).addValue(responseTime);
            logger.debug(pstmt.toString());
            lock.lock();
            iqTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    // 12 AP Queries
    public ClientResult execQ1(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int custid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q1());
            custid = rg.getRandomint(customer_no);
            pstmt.setInt(1,custid);
            pstmt.setInt(2,custid);
            pstmt.setInt(3,custid);
            pstmt.setInt(4,custid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            logger.debug(pstmt.toString());
            responseTime = currentEndTs - currentStarttTs;
            hist.getAPItem(0).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ2(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        PreparedStatement stmt = null;
        int custid = 1;
        try{
            stmt = conn.prepareStatement(sqls.ap_q2_1());
            stmt.setInt(1,customer_no);
            ResultSet rs = stmt.executeQuery();
            if ( rs.next() ) {
                custid= rs.getInt(1);
            }
            rs.close();
        } catch (SQLException throwables) {
            throwables.printStackTrace();
        }finally {
            try {
                stmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }

        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q2());
            pstmt.setInt(1,custid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            hist.getAPItem(1).addValue(responseTime);
            logger.debug(pstmt.toString());
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ3(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int custid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q3());
            custid = rg.getRandomint(customer_no);
            pstmt.setInt(1,custid);
            pstmt.setInt(2,custid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(2).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ4(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int custid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q4());
            custid = rg.getRandomint(customer_no);
            pstmt.setInt(1,custid);
            pstmt.setInt(2,custid);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(3).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ5(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int companyid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q5());
            companyid = rg.getRandomint(customer_no,customer_no+company_no);
            pstmt.setInt(1,companyid);

            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(4).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ6(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q6());
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(5).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ7(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int companyid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q7());
            //int cid = rg.getRandomint(10000);
            companyid = rg.getRandomint(customer_no,customer_no+company_no);
            pstmt.setInt(1,companyid);
            pstmt.setInt(2,companyid);
            pstmt.setInt(3,companyid);
            pstmt.setInt(4,companyid);

            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(6).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ8(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q8());
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(7).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ9(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q9());
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(8).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ10(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q10());
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(9).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ11(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q11());
            long currentStarttTs = System.currentTimeMillis();
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(10).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ12(Connection conn){
        ClientResult cr = new ClientResult();
        String Category = rg.getRandomCategory();
        PreparedStatement pstmt = null;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q12());
            long currentStarttTs = System.currentTimeMillis();
            pstmt.setString(1, Category);
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            logger.debug(pstmt.toString());
            responseTime = currentEndTs - currentStarttTs;
            hist.getAPItem(11).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    public ClientResult execQ13(Connection conn){
        ClientResult cr = new ClientResult();
        PreparedStatement pstmt = null;
        int custid;
        long responseTime = 0L;
        try {
            pstmt = conn.prepareStatement(sqls.ap_q13());
            custid = rg.getRandomint(customer_no);
            long currentStarttTs = System.currentTimeMillis();
            pstmt.setInt(1,custid);
            pstmt.execute();
            long currentEndTs = System.currentTimeMillis();
            responseTime = currentEndTs - currentStarttTs;
            logger.debug(pstmt.toString());
            hist.getAPItem(12).addValue(responseTime);
            lock.lock();
            apTotalCount++;
            lock.unlock();
        } catch (SQLException e) {
            e.printStackTrace();
            cr.setResult(false);
            cr.setErrorMsg(e.getMessage());
            cr.setErrorCode(String.valueOf(e.getErrorCode()));
        }  finally {
            cr.setRt(responseTime);
            try {
                pstmt.close();
            } catch (SQLException e) {
                e.printStackTrace();
            }
        }
        return cr;
    }

    @Override
    public ClientResult execute() {
        int type = getTaskType();
        Connection conn = ConnectionMgr.getConnection(1);
        ClientResult ret = new ClientResult();
        long totalElapsedTime = 0L;
        try {
            Class<APClient> apClass = (Class<APClient>)Class.forName("com.hybench.workload.APClient");
            if(type == 2){
                for(int r = 1;r <= round ;r++) {
                    long roundElapsedTime = 0L;
                    for (int i = 1; i <= 13; i++) {
                        Method method = apClass.getMethod("execQ" + i, Connection.class);
                        ClientResult cr = (ClientResult) method.invoke(this, conn);
                        logger.info("AP Task Q" + i + " elapsed time is  " + String.format("%.2f", cr.getRt()) + "(ms)");
                        totalElapsedTime += cr.getRt();
                        roundElapsedTime += cr.getRt();
                        apTotalTime += cr.getRt();
                    }
                    logger.info( r + " round elapsed time is " + String.format("%d",roundElapsedTime) + "(ms) ,QPS is " + String.format("%.2f",12000.0/roundElapsedTime));
                }
                ret.setRt(totalElapsedTime);
                ret.setApRound(round);
                logger.info("total elapsed time is  " + String.format("%.2f",ret.getRt()) + "(ms)");
            }
            else if(type == 7){
                int round = 1;
                int[] configuredSequence = configuredApQuerySequence();
                int workerOffset = 0;
                String threadName = Thread.currentThread().getName();
                if (threadName != null && threadName.length() > 1 && threadName.charAt(0) == 'T') {
                    try {
                        workerOffset = Math.max(0, Integer.parseInt(threadName.substring(1)) - 1);
                    } catch (NumberFormatException ignored) {
                        workerOffset = 0;
                    }
                }
                int sequenceIndex = configuredSequence != null && configuredSequence.length > 0
                        ? workerOffset % configuredSequence.length
                        : 0;
                int executedQueries = 0;
                int sequenceLimit = intParameter("ap_query_sequence_limit", 0);
                int[] configuredQueryIds = configuredApQueryIds();
                int[] configuredWeights = null;
                if (configuredSequence != null) {
                    logger.info("AP fixed query sequence enabled: "
                            + formatApQuerySequence(configuredSequence)
                            + (sequenceLimit > 0 ? ", limit=" + sequenceLimit + " per worker" : ""));
                }
                else if (configuredQueryIds != null) {
                    configuredWeights = configuredApQueryWeights(configuredQueryIds.length);
                    logger.info("AP weighted query selection enabled: " + formatApWeightedConfig(configuredQueryIds, configuredWeights));
                }
                while(!exitFlag){
                    long roundElapsedTime = 0L;
                    if (configuredSequence != null || configuredQueryIds != null) {
                        if (configuredSequence != null && sequenceLimit > 0 && executedQueries >= sequenceLimit) {
                            logger.info("Current thread " + Thread.currentThread().getName()
                                    + " - fixed AP query sequence limit reached: " + sequenceLimit);
                            break;
                        }
                        int queryId = configuredSequence != null
                                ? configuredSequence[sequenceIndex++ % configuredSequence.length]
                                : chooseWeightedApQuery(configuredQueryIds, configuredWeights);
                        Method method = apClass.getMethod("execQ" + queryId, Connection.class);
                        ClientResult cr = (ClientResult) method.invoke(this, conn);
                        totalElapsedTime += cr.getRt();
                        roundElapsedTime += cr.getRt();
                        executedQueries++;
                        if (!cr.isSucceed()) {
                            String msg = "Current thread " + Thread.currentThread().getName()
                                    + (configuredSequence != null ? " - fixed" : " - weighted")
                                    + " Q" + queryId + " failed";
                            logger.error(msg);
                            ret.setResult(false);
                            ret.setRt(totalElapsedTime);
                            System.exit(2);
                            throw new RuntimeException(msg);
                        }
                        if (exitFlag) {
                            break;
                        }
                        logger.info("Current thread " + Thread.currentThread().getName()
                                + (configuredSequence != null ? " - fixed" : " - weighted")
                                + " Q" + queryId
                                + " elapsed time is " + String.format("%d", roundElapsedTime) + "(ms)");
                        round++;
                        continue;
                    }
                    ArrayList<Integer> runList = getRandomList(1,13);
                    StringBuilder sb = new StringBuilder("Current thread " + Thread.currentThread().getName() + " - Run List in current round "+ round+" : ");
                    for(int idx:runList){
                        sb.append("Q").append(idx).append(" ");
                    }
                    logger.info(sb.toString());
                    int i = 0;
                    for ( i = 0; i < 13; i++) {
                        Method method = apClass.getMethod("execQ" + runList.get(i), Connection.class);
                        ClientResult cr = (ClientResult) method.invoke(this, conn);
                        totalElapsedTime += cr.getRt();
                        roundElapsedTime += cr.getRt();
                    }
                    if (exitFlag) {
                        break;
                    }
                    logger.info("Current thread " + Thread.currentThread().getName() + " - round " + round + " elapsed time is " + String.format("%d",roundElapsedTime) + "(ms)");
                    logger.info("Current thread " + Thread.currentThread().getName() + " - round " + round + " is done");
                    round++;
                }
                ret.setApRound(round);
                ret.setRt(totalElapsedTime);
            }
            else if(type== 0 || type == 4) {
                int[] configuredSequence = configuredApQuerySequence();
                int workerOffset = 0;
                String threadName = Thread.currentThread().getName();
                if (threadName != null && threadName.length() > 1 && threadName.charAt(0) == 'T') {
                    try {
                        workerOffset = Math.max(0, Integer.parseInt(threadName.substring(1)) - 1);
                    } catch (NumberFormatException ignored) {
                        workerOffset = 0;
                    }
                }
                int sequenceIndex = configuredSequence != null && configuredSequence.length > 0
                        ? workerOffset % configuredSequence.length
                        : 0;
                int executedQueries = 0;
                int sequenceLimit = intParameter("ap_query_sequence_limit", 0);
                int[] configuredQueryIds = configuredApQueryIds();
                int[] configuredWeights = null;
                if (configuredSequence != null) {
                    logger.info("XP AP fixed query sequence enabled: "
                            + formatApQuerySequence(configuredSequence)
                            + ", start_offset=" + sequenceIndex
                            + (sequenceLimit > 0 ? ", limit=" + sequenceLimit + " per worker" : ""));
                }
                else if (configuredQueryIds != null) {
                    configuredWeights = configuredApQueryWeights(configuredQueryIds.length);
                    logger.info("XP AP weighted query selection enabled: "
                            + formatApWeightedConfig(configuredQueryIds, configuredWeights));
                }
                while(!exitFlag){
                    Method method;
                    if (configuredSequence != null || configuredQueryIds != null) {
                        if (configuredSequence != null && sequenceLimit > 0 && executedQueries >= sequenceLimit) {
                            logger.info("Current thread " + Thread.currentThread().getName()
                                    + " - fixed XP AP query sequence limit reached: " + sequenceLimit);
                            break;
                        }
                        int queryId = configuredSequence != null
                                ? configuredSequence[sequenceIndex++ % configuredSequence.length]
                                : chooseWeightedApQuery(configuredQueryIds, configuredWeights);
                        method = apClass.getMethod("execQ" + queryId, Connection.class);
                        executedQueries++;
                    }
                    else {
                        int i = rg.getRandomint(1, 7);
                        method = apClass.getMethod("execIQ" + i,Connection.class);
                    }
                    ClientResult cr = (ClientResult) method.invoke(this, conn);
                    totalElapsedTime += cr.getRt();
                    if (!cr.isSucceed()) {
                        ret.setResult(false);
                        ret.setRt(totalElapsedTime);
                        System.exit(2);
                        throw new RuntimeException("Current thread " + Thread.currentThread().getName()
                                + " - XP AP query failed");
                    }
                    if(exitFlag)
                        break;
                }
                ret.setRt(totalElapsedTime);
            }

        } catch (ClassNotFoundException e) {
            ret.setResult(false);
            e.printStackTrace();
        } catch (NoSuchMethodException e) {
            ret.setResult(false);
            e.printStackTrace();
        } catch (InvocationTargetException e) {
            ret.setResult(false);
            e.printStackTrace();
        } catch (IllegalAccessException e) {
            ret.setResult(false);
            e.printStackTrace();
        } catch (Exception e) {
            ret.setResult(false);
            e.printStackTrace();
        } finally {
            if(conn!=null){
                try {
                    conn.close();
                } catch (SQLException e) {
                    e.printStackTrace();
                }
            }
        }
        return ret;
    }
}
