# DDPG 
# Use parallel env as a quicker way to sample exprs 
# Not as a way to solve sample inefficiency problem
# as in many on-policy algorithms

from collections import deque, defaultdict
from copy import deepcopy
import time
import numpy as np
import gym
import os

import torch
import torch.optim as optim
import torch.nn.functional as F
from torch.nn.utils import clip_grad_norm_

import rlkits.utils.logger as logger
from rlkits.policies import DeterministicPolicy
from rlkits.policies import QNetForContinuousAction
from rlkits.memory import Memory
from rlkits.evaluate import evaluate_policy
from rlkits.env_batch import ParallelEnvBatch
from rlkits.env_wrappers import AutoReset, StartWithRandomActions

from rlkits.running_mean_std import RunningMeanStd

def to_tensor(*args):
    new_args = []
    for arg in args:
        assert isinstance(arg, np.ndarray)
        if arg.dtype == np.float64:
            arg = arg.astype(np.float32)
        new_args.append(torch.from_numpy(arg))
    return new_args


def DDPG(*,
    env_name,
    nenvs,
    nsteps,
    niters,
    nupdates,
    buf_size,
    gamma,
    pi_lr,
    v_lr,
    polyak,
    batch_size,
    log_interval,
    max_grad_norm,
    l2_weight_decay,
    clip_action,
    log_dir,
    ckpt_dir,
    **network_kwargs,
):
    """
    env: gym env (parallel)
    nsteps: number of steps to sample from the parallel env
        nstep * env.nenvs frames will be sampled
    
    nenvs: number of parallel envs
    
    niters: total number of iteration
        one iteration consists of 
        1. sample `nsteps * env.nenvs` number of frames and cache to 
            memory buffer
        2. train the agent for `nupdates` number of times. Each time
            using `batch_size` number of frames
            
    ployak (float): linear interpolation coefficient for updating 
        the target policy and value net from the current ones; 
        Interpret it as the weight of the current target network
    
    buf_size: size of the replay buffer
    
    clip_action: clip the action to [-1, 1]
    """
    # env 
    def make_env():
        env = gym.make(env_name)
        env = AutoReset(env)
        env = StartWithRandomActions(env, max_random_actions=5)
        return env
    
    env = ParallelEnvBatch(make_env, nenvs=nenvs)
    
    
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    if not os.path.exists(ckpt_dir):
        os.makedirs(ckpt_dir)
    logger.configure(dir=log_dir) 
        
    ob_space = env.observation_space
    ac_space = env.action_space
    
    
    # normalize action
    # clip the action to [-1, 1]
    action_high = ac_space.high
    action_low = ac_space.low
    
    # map [action_low, action_high] -> [-1, 1]
    if clip_action:
        action_range = [-1, 1]
    else:
        action_range = [action_low, action_high]
        
    # during rollout 
    # ac = policy(obs)
    # remap action back to [action_low, action_high]
    # ac -> (high - low)/2(ac) + (high + low)/2
    def normalize(x, low, high):
        """r \in [low, high] -> [-1, 1]"""
        return (2*x - (high + low))/(high - low)
    
    def denormalize(y, low, high):
        """inverse of `normalize` """
        return (high - low)*y / 2 + (high + low)/2
    
    policy = DeterministicPolicy(
        ob_space=ob_space, ac_space=ac_space, action_range=action_range,
        ckpt_dir=ckpt_dir,
        **network_kwargs
    )
    target_policy = DeterministicPolicy(
        ob_space=ob_space, ac_space=ac_space, action_range=action_range,
        ckpt_dir=ckpt_dir,
        **network_kwargs
    )
    target_policy.model.load_state_dict(policy.model.state_dict())
    
    value_net = QNetForContinuousAction(
        ob_space=ob_space, ac_space=ac_space, ckpt_dir=ckpt_dir,
        **network_kwargs
    )
    
    target_value_net = QNetForContinuousAction(
        ob_space=ob_space, ac_space=ac_space, ckpt_dir=ckpt_dir,
        **network_kwargs
    )
    target_value_net.model.load_state_dict(value_net.model.state_dict())
    
    poptimizer = optim.Adam(policy.parameters(), lr=pi_lr, 
                            weight_decay=l2_weight_decay)
    voptimizer = optim.Adam(value_net.parameters(), lr=v_lr,
                            weight_decay=l2_weight_decay)
    
    replay_buffer = Memory(
        limit=buf_size,
        action_shape=ac_space.shape,
        observation_shape=ob_space.shape
    )

    # keep track of running mean and std
    # of observations
    obs_rms = RunningMeanStd(shape=ob_space.shape)
    

    best_ret = np.float('-inf')
    rolling_buf_episode_rets = deque(maxlen=10) 
    curr_state = env.reset()
    for i in range(1, 1 + niters):
        # sample nsteps experiences and save to replay buffer
        for _ in range(nsteps):
            ac = policy.step(
                *to_tensor(obs_rms.normalize(curr_state))
            )
            
            if clip_action:
                ac = denormalize(ac, action_low, action_high)
                
            nx, rew, done, _ = env.step(ac)
            obs_rms.update(np.array([curr_state]))
            
            # action saved in replay buffer is not normalized
            replay_buffer.append(
                obs0=curr_state, action=ac, reward=rew, obs1=nx, terminal1=done
            )
            curr_state = nx
                
        # train the policy and value
        lossvals = defaultdict(list)
        for j in range(nupdates):
            res = replay_buffer.sample(batch_size)
            obs, acs, rews, nxs, dones = res['obs0'], res['actions'], res['rewards'], res['obs1'], res['terminals1']
            
            # normalize observation and nx observation
            obs = obs_rms.normalize(obs)
            nxs = obs_rms.normalize(nxs)
            acs = normalize(acs, action_low, action_high)
            
            # actions saved in replay buffer is not normalized
            # but we don't need to use the action in the replay buffer
            # to compute the next state action value
            obs, acs, rews, nxs, dones = to_tensor(
                obs, acs, rews, nxs, dones
            )
            
            # target for qnet
            with torch.no_grad():
                nx_state_vals = target_value_net(
                    nxs, target_policy(nxs)
                )
            # Q_targ(s', \mu_targ(s'))
            q_targ = rews + gamma * (1 - dones) * nx_state_vals
            
            # Q(s, a)
            # actions in replay buffer is used to calculate 
            # critic loss
            q_pred = value_net(obs, acs)
            q_loss = F.mse_loss(q_pred, q_targ)
            voptimizer.zero_grad()
            q_loss.backward()
            clip_grad_norm_(value_net.parameters(), max_norm=max_grad_norm)
            voptimizer.step()

            # update policy through value
            policy_loss = -value_net(obs, policy(obs)).mean()
            voptimizer.zero_grad()
            poptimizer.zero_grad()
            policy_loss.backward()
            clip_grad_norm_(policy.parameters(), max_norm=max_grad_norm)
            poptimizer.step()
            
            lossvals['policy_loss'].append(policy_loss.detach().numpy())
            lossvals['value_loss'].append(q_loss.detach().numpy())
            lossvals['target_value'].append(q_targ.detach().numpy())
            lossvals['predicted_q_value'].append(q_pred.detach().numpy())
        
            # update target value net and policy
            # through linear interpolation
            for p, p_targ in zip(policy.parameters(), target_policy.parameters()):
                p_targ.data.copy_(polyak*p_targ.data + (1-polyak)*p.data)

            for p, p_targ in zip(value_net.parameters(),target_value_net.parameters()):
                p_targ.data.copy_(polyak*p_targ.data + (1-polyak)*p.data)
        
        
        if i % log_interval == 0 or i == 1:
            # loss from policy and value 
            for k, v in lossvals.items():
                logger.record_tabular(k, np.mean(v))
            
            
            
            # evaluate the policy $n_trials times
            # TODO use parallel env sampler here
            rews = evaluate_policy(env_name, policy)

            rolling_buf_episode_rets.extend(sum(rews))
            ret = np.mean(rolling_buf_episode_rets)
            
            logger.record_tabular("ma_ep_ret", ret)
            logger.record_tabular("mean_step_rew", np.mean(rews))

            pw, tpw = policy.average_weight(), target_policy.average_weight()
            vw, tvw = value_net.average_weight(), target_value_net.average_weight()
            logger.record_tabular("policy_net_weight", pw)
            logger.record_tabular("target_policy_net_weight", tpw)
            logger.record_tabular("value_net_weight", vw)
            logger.record_tabular("target_value_net_weight", tvw)
            
            logger.dump_tabular()
            if ret > best_ret:
                best_ret = ret
                policy.save_ckpt('best')
                value_net.save_ckpt('best')
                torch.save(poptimizer, os.path.join(ckpt_dir, 
                                                    'poptim-best.pth'))
                torch.save(voptimizer, os.path.join(ckpt_dir, 
                                                    'voptim-best.pth'))
    
    policy.save_ckpt('final')
    value_net.save_ckpt('final')
    torch.save(poptimizer, os.path.join(ckpt_dir, 'poptim-final.pth'))
    torch.save(voptimizer, os.path.join(ckpt_dir, 'voptim-final.pth'))
    env.close()
    return 


if __name__=='__main__':
    from rlkits.env_batch import ParallelEnvBatch
    from rlkits.env_wrappers import AutoReset, StartWithRandomActions
    
    def make_env():
        env = gym.make('Pendulum-v0')
        env = AutoReset(env)
        env = StartWithRandomActions(env, max_random_actions=5)
        return env

    DDPG(
        env_name='Pendulum-v0',
        nsteps=32,
        nenvs=8,
        niters=10000,
        nupdates=20,
        buf_size=10000, 
        gamma=0.99,
        pi_lr=1e-4,
        v_lr=1e-4,
        polyak=0.5,
        model_update_frequency=5,
        batch_size=128,
        log_interval=1,
        max_grad_norm=0.1,
        log_dir="/tmp/ddpg",
        ckpt_dir="/tmp/ddpg",
        hidden_layers=[256, 256, 64]
    )
    
    # 32 x 8 = 256 exprs sampled per iter
    # 256000