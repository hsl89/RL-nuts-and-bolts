# When computing loss of a policy, should we use the action from the
# replay buffer (older policy), or should we use the action sampled
# from the current policy ?

# According to SAC paper, it seems that value functions are updated
# using actions sampled from the current policy, whereas the policy is
# updated through the actions sampled from the current policy via
# reparametrization trick. Why it makes sense to do it this way?

# For value net update, using the action sampled from the current
# policy amounts to a better label. The current policy makes an
# action so that the corresponding state action value Q(s, a) is a more
# accurate approximation of the state value V(s).

# For policy update, we need the policy gradient of the current
# policy, so naturally we should sample an action (for a state
# retrieved from replay buffer).

# In DDPG, the same better label for value function is given by
# the state action approximation of the target net over action
# sampled from target policy (on the next state).

# the reparametrization makes the distribution of
# acs_curr independent from the parameters of the
# policy net.
# reparametrization is a way to write the expectation
# independent from the parameter

# The advantage of reparametrization
# https://gregorygundersen.com/blog/2018/04/29/reparameterization/
# 1. It allows us to re-write gradient of expectation
# as expectation of gradient. Hence, we can use Monte
# Carlo method to estimate the gradient.
# 2. Stability: reparametrization limits the variance
# of the estimate. It basically caps the variance of
# the estimate to the variance of N(0,1)
# To see how reparam helps stability, checkout
# https://nbviewer.jupyter.org/github/gokererdogan/Notebooks/blob/master/Reparameterization%20Trick.ipynb

from rlkits.memory import Memory
from rlkits.utils import to_tensor
from rlkits.policies import QNetForContinuousAction
from rlkits.policies import Policy
from copy import deepcopy
import torch
import torch.nn.functional as F

def compute_loss(policy, Q1, Q1_targ, Q2, Q2_targ,
                 batch, gamma, alpha):
    obs, acs, rews, nxs, dones = batch['obs0'], batch['actions'],\
        batch['rewards'], batch['obs1'], batch['terminals1']

    obs, acs, rews, nxs, dones = to_tensor(
        obs, acs, rews, nxs, dones)
    
    # compute the target for the Q-nets
    # treat nxa and nxa_logprob as constant
    with torch.no_grad():
        nxa, nxa_lopprob = policy(nxs)
        nxv = torch.minimum(
            Q1_targ(nxs, nxa.detach()), Q1_targ(nxs, nxa.detach())
        )
    
    assert rews.shape == nxv.shape, f"{rews.shape}, {nxv.shape}"    
    y = rews + gamma*(1-dones)*(nxv - alpha*nxa_logprob.detach())

    # compute value loss
    q1, q2 = Q1(obs, acs), Q2(obs, acs)
    Q1_loss = F.mse_loss(q1, y)
    Q2_loss = F.mse_loss(q2, y)
    
    # pytorch support multiple forward pass
    # all computation graphs are saved
    # https://discuss.pytorch.org/t/multiple-forward-passes-single-conditional-backward-pass/99277
    # this means I can compute loss f
    
    # compute policy loss
    # objective is to maximize 
    # Q(s, a) - \alpha \log \pi(a | s), a sampled from the current policy
    acs_t, acs_t_logprob = policy.step(obs, no_grad=False)
    policy_loss = -(torch.minimum(Q1(obs, acs_t), Q2(obs, acs_t)) - alpha*acs_t_logprob)
    
    res = {
            "policy_loss": policy_loss,
            "Q1_loss": Q1_loss,
            "Q2_loss": Q2_loss
          }


def SAC(*,
        env_name,
        nsteps,
        buf_size,
        warm_up_steps,
        gamma,
        alpha,
        polyak,
        batch_size,
        log_dir,
        ckpt_dir,
        **network_kwargs
        ):
    
    # env
    def make_env():
        env = gym.make(env_name)
        env = StartWithRandomActions(env, max_random_actions=5)
        env = RecoverAction(env)
        return env

    env = make_env()

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    if not os.path.exists(ckpt_dir):
        os.makedirs(ckpt_dir)
    logger.configure(dir=log_dir)

    ob_space = env.observation_space
    ac_space = env.action_space
    print("======", f"action space shape {ac_space.shape}, \
          ob space shape: {ob_space.shape}", "======")
    
    # Q-net
    Q1 = QNetForContinuousAction(
        ob_space=ob_space, ac_space=ac_space,
        ckpt_dir=ckpt_dir,
        **network_kwargs
        )
    Q2 = deepcopy(Q1)
    
    # target Q-net
    Q1_targ = deepcopy(Q1)
    Q2_targ = deepcopy(Q1)
    
    # policy 
    policy = SacPolicy(
        ob_space=ob_space, ac_space=ac_space, ckpt_dir=ckpt_dir,
        **network_kwargs
    )

    # memory buffer
    replay_buffer = Memory(
        limit=buf_size,
        action_shape=ac_space.shape
    )

    
    best_ret = np.float('-inf')
    rolling_buf_episode_rets = deque(maxlen=100)
    curr_state = env.reset()
    policy.reset()

    step=0
    while step <= nsteps:
        if step < warm_up_steps:
            action = policy.random_action()
        else:
            action, _ = policy.step(curr_state, no_grad=True)
        nx, rew, done, _ = env.step(action)
        # record to the replay buffer
        assert nx.shape == ob_space.shape, f"{nx.shape},{ob_space.shape}"
        assert action.shape == ac_space.shape, f"{action.shape},{ac_space.shape}"
        replay_buffer.append(
            obs0=curr_state, action=action, reward=rew, obs1=nx, terminal1=done
        )
        episode_rews += rew
        if done:
            curr_state = env.reset()
            policy.reset()  # reset random process
            rolling_buf_episode_rets.append(episode_rews)
            episode_rews = 0
        else:
            curr_state = nx

        # train after warm up steps
        if step < warm_up_steps: continue
        batch = replay_buffer.sample(batch_size)
        losses = compute_loss(policy, Q1, Q2, batch,
                              gamma, alpha)
